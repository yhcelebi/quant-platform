"""qp.data.quality — Data quality checks; results go to ops.data_quality_results."""

from qp.data.quality.checks import CheckResult, QualityStatus
from qp.data.quality.runner import check_tcmb_fx, save_results

__all__ = ["CheckResult", "QualityStatus", "check_tcmb_fx", "save_results"]
