import pytest
from k8s_collector import DeploymentData, ContainerSpec
from analyzer import workload


def _make_container(**kwargs) -> ContainerSpec:
    defaults = dict(
        name="app",
        image="myimage:latest",
        ports=[{"port": 8080, "protocol": "TCP", "name": "http"}],
        resources={"limits": {"memory": "512Mi", "cpu": "1"}, "requests": {}},
        env=[],
        volume_mounts=[],
        liveness_probe={"timeout_seconds": 3, "period_seconds": 10},
        readiness_probe={"timeout_seconds": 3, "period_seconds": 5},
        startup_probe=None,
        security_context={"privileged": False},
    )
    defaults.update(kwargs)
    return ContainerSpec(**defaults)


def _make_deployment(containers=None, volumes=None, **kwargs) -> DeploymentData:
    defaults = dict(
        name="test-svc",
        namespace="default",
        replicas=1,
        containers=containers or [_make_container()],
        init_containers=[],
        volumes=volumes or [],
        service_account="default",
        annotations={},
        labels={},
    )
    defaults.update(kwargs)
    return DeploymentData(**defaults)


def _ids(checks):
    return [c.id for c in checks]


def _kinds(checks):
    return {c.id: c.kind for c in checks}


class TestPVCDetection:
    def test_pvc_volume_is_blocker(self):
        dep = _make_deployment(volumes=[{"name": "data", "type": "pvc", "claim_name": "my-pvc"}])
        checks = workload.analyze(dep)
        assert "pvc_volume" in _ids(checks)
        assert _kinds(checks)["pvc_volume"] == "blocker"

    def test_no_pvc_no_blocker(self):
        dep = _make_deployment(volumes=[])
        checks = workload.analyze(dep)
        assert "pvc_volume" not in _ids(checks)


class TestPrivilegedMode:
    def test_privileged_true_is_blocker(self):
        c = _make_container(security_context={"privileged": True})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "privileged_container" in _ids(checks)
        assert _kinds(checks)["privileged_container"] == "blocker"

    def test_privileged_false_no_blocker(self):
        c = _make_container(security_context={"privileged": False})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "privileged_container" not in _ids(checks)

    def test_privileged_none_no_blocker(self):
        c = _make_container(security_context={"privileged": None})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "privileged_container" not in _ids(checks)


class TestMemoryOverLimit:
    def test_memory_over_32gib_is_blocker(self):
        c = _make_container(resources={"limits": {"memory": "33Gi", "cpu": "1"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "memory_over_limit" in _ids(checks)
        assert _kinds(checks)["memory_over_limit"] == "blocker"

    def test_memory_exactly_32gib_no_blocker(self):
        c = _make_container(resources={"limits": {"memory": "32Gi", "cpu": "1"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "memory_over_limit" not in _ids(checks)

    def test_memory_in_mib_over_limit(self):
        c = _make_container(resources={"limits": {"memory": "33792Mi", "cpu": "1"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "memory_over_limit" in _ids(checks)

    def test_memory_under_limit_no_blocker(self):
        c = _make_container(resources={"limits": {"memory": "1Gi", "cpu": "1"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "memory_over_limit" not in _ids(checks)


class TestMissingHealthProbes:
    def test_no_probes_is_warning(self):
        c = _make_container(liveness_probe=None, readiness_probe=None)
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "no_health_probes" in _ids(checks)
        assert _kinds(checks)["no_health_probes"] == "warning"

    def test_liveness_only_no_warning(self):
        c = _make_container(liveness_probe={"timeout_seconds": 3, "period_seconds": 10}, readiness_probe=None)
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "no_health_probes" not in _ids(checks)

    def test_both_probes_no_warning(self):
        c = _make_container(
            liveness_probe={"timeout_seconds": 3, "period_seconds": 10},
            readiness_probe={"timeout_seconds": 3, "period_seconds": 5},
        )
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "no_health_probes" not in _ids(checks)


class TestConfigMapEnvVars:
    def test_configmap_env_is_warning(self):
        c = _make_container(env=[{"name": "CONFIG_VAL", "from": "configmap", "ref": "my-cm"}])
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "configmap_env_var" in _ids(checks)
        assert _kinds(checks)["configmap_env_var"] == "warning"

    def test_secret_env_is_positive(self):
        c = _make_container(env=[{"name": "DB_PASS", "from": "secret", "ref": "my-secret"}])
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "secret_env_var" in _ids(checks)
        assert _kinds(checks)["secret_env_var"] == "positive"

    def test_plain_env_no_check(self):
        c = _make_container(env=[{"name": "PORT", "value": "8080"}])
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "configmap_env_var" not in _ids(checks)
        assert "secret_env_var" not in _ids(checks)


class TestHostPathVolume:
    def test_hostpath_is_blocker(self):
        dep = _make_deployment(volumes=[{"name": "host", "type": "hostPath", "path": "/var/log"}])
        checks = workload.analyze(dep)
        assert "hostpath_volume" in _ids(checks)
        assert _kinds(checks)["hostpath_volume"] == "blocker"


class TestConfigMapVolumeMount:
    def test_configmap_volume_is_blocker(self):
        dep = _make_deployment(volumes=[{"name": "cfg", "type": "configmap", "name_ref": "app-config"}])
        checks = workload.analyze(dep)
        assert "configmap_volume_mount" in _ids(checks)
        assert _kinds(checks)["configmap_volume_mount"] == "blocker"


class TestCPUOverLimit:
    def test_cpu_over_8_is_blocker(self):
        c = _make_container(resources={"limits": {"cpu": "9", "memory": "1Gi"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "cpu_over_limit" in _ids(checks)
        assert _kinds(checks)["cpu_over_limit"] == "blocker"

    def test_cpu_millicore_over_limit(self):
        c = _make_container(resources={"limits": {"cpu": "9000m", "memory": "1Gi"}, "requests": {}})
        dep = _make_deployment(containers=[c])
        checks = workload.analyze(dep)
        assert "cpu_over_limit" in _ids(checks)


class TestHostNetwork:
    def test_host_network_true_is_blocker(self):
        dep = _make_deployment(host_network=True)
        checks = workload.analyze(dep)
        assert "host_network" in _ids(checks)
        assert _kinds(checks)["host_network"] == "blocker"

    def test_host_network_false_no_blocker(self):
        dep = _make_deployment(host_network=False)
        checks = workload.analyze(dep)
        assert "host_network" not in _ids(checks)
