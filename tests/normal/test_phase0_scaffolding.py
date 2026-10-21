"""
Normal Tests: Phase 0.1 Scaffolding & Zero-Dep / Zero-UI Compliance.
"""
import sys
import os
from pathlib import Path
from onyxsi.contracts import OnyxErrorCode, OnyxError, DataContract, ConfigContract
from onyxsi.auditor import ZeroDepAndZeroUiAuditor, ViolationType


def test_zero_dependency_and_zero_ui_on_source():
    src_dir = Path(__file__).parent.parent.parent / "src"
    auditor = ZeroDepAndZeroUiAuditor(workspace_root=str(src_dir))
    result = auditor.audit_directory(src_dir)
    assert result.passed, f"Violations found: {[v.to_dict() for v in result.violations]}"
    assert result.files_scanned > 0
    assert len(result.violations) == 0


def test_contracts_data_validation():
    contract = DataContract(name="WorkerTask", fields={"task_id": str, "priority": int})
    assert contract.validate({"task_id": "T-01", "priority": 1}) is True
    assert contract.validate({"task_id": "T-01", "priority": "high"}) is False
    assert contract.validate({"task_id": "T-01"}) is False


def test_error_code_formatting():
    err = OnyxError(OnyxErrorCode.E0001, "Banned dependency detected")
    err_dict = err.to_dict()
    assert err_dict["error"]["code"] == "E0001"
    assert err_dict["error"]["code_value"] == 1001
    assert "Banned dependency" in err_dict["error"]["message"]
