"""Effective coverage, separate from historical detector scores and eligibility."""


def report_complete(report, algorithm_id=None):
    if report.get('assessment_complete') is False or report.get('coverage_complete') is False:
        return False
    coverage = report.get('check_coverage')
    if coverage is None:
        # Old numeric reports did not record which physical subchecks ran.
        return algorithm_id != 'Numeric_Check'
    if not isinstance(coverage, dict) or coverage.get('coverage_complete') is not True:
        return False
    checks = coverage.get('checks')
    return bool(checks) and all(
        isinstance(check, dict) and check.get('status') in {'evaluated', 'not_applicable'}
        for check in checks)


def record_complete(record):
    reports = (record.get('result') or {}).get('reports') or []
    return bool(reports) and record.get('status') == 'completed' and (
        record.get('scope_complete', True)) and all(
        report_complete(report, record['algorithm']['id']) for report in reports)
