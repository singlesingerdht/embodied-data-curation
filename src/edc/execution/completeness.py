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


def report_scope_complete(report):
    """An explicitly selected episode fragment cannot complete the whole episode.

    Missing declarations retain compatibility with existing full-input methods.
    Read the native declaration as well as the envelope so historical records
    with a default scope_complete=True cannot release a partially assessed episode.
    """
    if 'assessment_scope' not in report:
        return True
    scope = report['assessment_scope']
    if not isinstance(scope, dict) or scope.get('whole_episode') is not True:
        return False
    count = scope.get('source_frame_count')
    bounds = scope.get('original_frame_range')
    return (type(count) is int and count > 0 and isinstance(bounds, list)
            and all(type(i) is int for i in bounds) and bounds == [0, count])


def record_complete(record):
    reports = (record.get('result') or {}).get('reports') or []
    return bool(reports) and record.get('status') == 'completed' and (
        record.get('scope_complete', True)) and all(
        report_complete(report, record['algorithm']['id']) and report_scope_complete(report)
        for report in reports)
