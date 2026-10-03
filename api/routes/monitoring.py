from api.schemas import APIError, parse_page, response


def list_runs(repository, params):
    limit, offset = parse_page(params)
    source = _one(params, "source")
    items = repository.list_monitoring_runs(source=source, limit=limit, offset=offset)
    return response(items, total=repository.count_monitoring_runs(source=source), limit=limit, offset=offset)


def run_detail(repository, run_id):
    item = repository.get_monitoring_run(run_id)
    if item is None:
        raise APIError(404, "not_found", "monitoring run not found")
    return response(item=item)


def _one(params, key):
    values = params.get(key)
    if not values:
        return None
    if len(values) != 1:
        raise APIError(400, "invalid_parameter", f"{key} may be specified once")
    return values[0]
