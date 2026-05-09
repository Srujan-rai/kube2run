import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrafficProfile:
    pattern_class: str              # STEADY_HIGH, STEADY_LOW, BURSTY, MODERATE, SCALES_TO_ZERO, BUSINESS_HOURS, BATCH, UNKNOWN
    volatility_ratio: float         # max_replicas / min_replicas (0 = unknown)
    current_utilization_pct: int    # current_replicas / max_replicas * 100
    at_peak_now: bool               # current replicas >= 90% of max
    at_baseline_now: bool           # current replicas <= min
    peak_rps_hint: Optional[int]    # from NGINX limit-rps annotation
    concurrency_hint: Optional[int] # from NGINX limit-connections annotation
    scale_to_zero: bool             # min=0 or KEDA min=0
    time_pattern: Optional[str]     # human-readable e.g. "KEDA cron Mon-Fri 9am-6pm"
    pdb_zero_downtime: bool         # PDB maxUnavailable=0 → 24/7 steady
    slow_scale_down: bool           # stabilizationWindowSeconds > 600
    recommended_min_instances: int
    recommended_max_instances: int
    recommended_concurrency: Optional[int]  # None = use memory-based fallback in scorer
    cost_signal: str                # SAVINGS_LIKELY, COMPARE_CAREFULLY, UNKNOWN
    signals: list = field(default_factory=list)


def analyze(deployment, hpa=None, ingresses=None, pdbs=None,
            keda_objects=None, cronjobs=None) -> TrafficProfile:
    ingresses = ingresses or []
    pdbs = pdbs or []
    keda_objects = keda_objects or []
    cronjobs = cronjobs or []
    signals = []

    current_replicas = deployment.replicas

    # ── HPA shape ────────────────────────────────────────────────────────────
    min_r = 1
    max_r = 1
    scale_to_zero = False
    scale_down_stab = None
    scale_up_stab = None

    if hpa:
        min_r = hpa.min_replicas if hpa.min_replicas is not None else 1
        max_r = hpa.max_replicas if hpa.max_replicas is not None else 1
        scale_to_zero = min_r == 0
        scale_down_stab = getattr(hpa, "scale_down_stabilization_seconds", None)

    volatility_ratio = (max_r / max(min_r, 1)) if hpa else 0.0

    # ── Pattern classification ────────────────────────────────────────────────
    if not hpa:
        pattern_class = "UNKNOWN"
    elif scale_to_zero:
        pattern_class = "SCALES_TO_ZERO"
    elif volatility_ratio >= 10:
        pattern_class = "BURSTY"
    elif volatility_ratio >= 3:
        pattern_class = "MODERATE"
    elif min_r >= 10:
        pattern_class = "STEADY_HIGH"
    else:
        pattern_class = "STEADY_LOW"

    # ── One-time snapshot: current utilization ────────────────────────────────
    if hpa and max_r > 0:
        current_utilization_pct = min(100, int(current_replicas / max_r * 100))
        at_peak_now = (current_replicas / max_r) >= 0.9   # Bug fix: was int() truncation
        at_baseline_now = current_replicas <= min_r

        if at_peak_now:
            state_label = "AT PEAK"
        elif at_baseline_now:
            state_label = "AT BASELINE"
        else:
            state_label = f"SCALING ({current_utilization_pct}% cap)"
        signals.append(
            f"Snapshot: {current_replicas}/{max_r} replicas running → {state_label}"
        )
    else:
        current_utilization_pct = 100
        at_peak_now = False
        at_baseline_now = False
        signals.append(f"Snapshot: {current_replicas} replicas running (no HPA)")

    # ── HPA range signal ─────────────────────────────────────────────────────
    if hpa:
        ratio_str = f"{volatility_ratio:.0f}x" if volatility_ratio < 1000 else "∞"
        signals.append(f"HPA {min_r}→{max_r} replicas ({ratio_str} volatility range)")
        if scale_to_zero:
            signals.append("HPA min=0 → scales to zero when idle")

    # ── HPA behavior signals ──────────────────────────────────────────────────
    slow_scale_down = False
    if scale_down_stab and scale_down_stab > 600:
        slow_scale_down = True
        mins = scale_down_stab // 60
        signals.append(
            f"HPA scale-down stabilization {mins}m → traffic has extended quiet periods"
        )

    # ── Ingress / NGINX rate-limit annotations ────────────────────────────────
    peak_rps_hint = None
    concurrency_hint = None
    for ing in ingresses:
        if ing.namespace != deployment.namespace:
            continue
        anns = ing.annotations
        limit_rps = anns.get("nginx.ingress.kubernetes.io/limit-rps")
        limit_conn = anns.get("nginx.ingress.kubernetes.io/limit-connections")
        if limit_rps and peak_rps_hint is None:
            try:
                peak_rps_hint = int(limit_rps)
                signals.append(
                    f"NGINX limit-rps: {peak_rps_hint} req/s → peak throughput cap"
                    f" (ingress: {ing.name})"
                )
            except ValueError:
                pass
        if limit_conn and concurrency_hint is None:
            try:
                concurrency_hint = int(limit_conn)
                signals.append(
                    f"NGINX limit-connections: {concurrency_hint} → max concurrent connections"
                )
            except ValueError:
                pass

    # ── PDB zero-downtime signal ──────────────────────────────────────────────
    pdb_zero_downtime = False
    for pdb in pdbs:
        if pdb.namespace != deployment.namespace:
            continue
        mu = pdb.max_unavailable
        ma = pdb.min_available
        if mu in ("0", "0%") or (mu is not None and str(mu) == "0"):
            pdb_zero_downtime = True
            signals.append(
                f"PDB '{pdb.name}' maxUnavailable=0 → zero-downtime required"
                " → 24/7 steady traffic likely"
            )
            break
        if ma in ("100%",):
            pdb_zero_downtime = True
            signals.append(
                f"PDB '{pdb.name}' minAvailable=100% → zero-downtime required"
                " → 24/7 steady traffic likely"
            )
            break

    # ── KEDA ScaledObject time pattern ────────────────────────────────────────
    time_pattern = None
    for obj in keda_objects:
        if obj.scale_target_ref != deployment.name or obj.namespace != deployment.namespace:
            continue
        for trigger in obj.triggers:
            if trigger.get("type") == "cron":
                md = trigger.get("metadata", {})
                start = md.get("start", "?")
                end = md.get("end", "?")
                tz = md.get("timezone", "UTC")
                time_pattern = f"Cron: {start} → {end} ({tz})"
                signals.append(f"KEDA cron trigger detected → {time_pattern}")
                if pattern_class not in ("SCALES_TO_ZERO",):
                    pattern_class = "BUSINESS_HOURS"
                scale_to_zero = True
                break
        if time_pattern:
            break

    # ── CronJob in namespace → batch traffic hint ─────────────────────────────
    for cj in cronjobs:
        if cj.namespace == deployment.namespace and not cj.suspend:
            signals.append(
                f"CronJob '{cj.name}' ({cj.schedule}) in namespace"
                " → periodic traffic spikes likely"
            )
            if pattern_class == "UNKNOWN":
                pattern_class = "BATCH"

    # ── Recommendations ───────────────────────────────────────────────────────
    if scale_to_zero:
        rec_min = 0
    elif pdb_zero_downtime:
        rec_min = max(1, min_r)
    elif pattern_class == "STEADY_HIGH":
        rec_min = min_r
    else:
        rec_min = max(0, min_r - 1)

    rec_max = math.ceil(max_r * 1.25) if hpa else max(10, current_replicas * 2)

    if peak_rps_hint and max_r > 0:
        rec_concurrency: Optional[int] = max(1, peak_rps_hint // max_r)
    elif concurrency_hint:
        rec_concurrency = concurrency_hint
    else:
        rec_concurrency = None  # scorer will use memory-based fallback

    # ── Cost signal ───────────────────────────────────────────────────────────
    if pattern_class in ("SCALES_TO_ZERO", "BUSINESS_HOURS", "BURSTY", "MODERATE", "BATCH"):
        cost_signal = "SAVINGS_LIKELY"
    elif pattern_class == "STEADY_HIGH":
        cost_signal = "COMPARE_CAREFULLY"
    else:
        cost_signal = "UNKNOWN"

    if not signals:
        signals.append("No HPA, ingress, or scheduling config found — pattern unknown")

    return TrafficProfile(
        pattern_class=pattern_class,
        volatility_ratio=volatility_ratio,
        current_utilization_pct=current_utilization_pct,
        at_peak_now=at_peak_now,
        at_baseline_now=at_baseline_now,
        peak_rps_hint=peak_rps_hint,
        concurrency_hint=concurrency_hint,
        scale_to_zero=scale_to_zero,
        time_pattern=time_pattern,
        pdb_zero_downtime=pdb_zero_downtime,
        slow_scale_down=slow_scale_down,
        recommended_min_instances=rec_min,
        recommended_max_instances=rec_max,
        recommended_concurrency=rec_concurrency,
        cost_signal=cost_signal,
        signals=signals,
    )
