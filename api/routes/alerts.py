from api.schemas import APIError, parse_bool, parse_page, response, validate_time


def list_alerts(repository, params):
    limit, offset = parse_page(params)
    since, until = validate_time(params, "since"), validate_time(params, "until")
    if since and until and since > until:
        raise APIError(400, "invalid_parameter", "since must not be later than until")
    items, total = repository.query_alerts(alert_level=_one(params, "alert_level"),
        active=parse_bool(params, "active"), source=_one(params, "source"),
        event_id=_one(params, "event_id"), since=since, until=until, limit=limit, offset=offset)
    return response(items, total=total, limit=limit, offset=offset)


def alert_detail(repository, alert_id):
    item = repository.get_alert(alert_id)
    if item is None:
        raise APIError(404, "not_found", "alert not found")
    item["history"] = repository.get_alert_history(alert_id)
    return response(item=item)


def alert_history(repository, alert_id):
    if repository.get_alert(alert_id) is None:
        raise APIError(404, "not_found", "alert not found")
    return response(repository.get_alert_history(alert_id))


def _one(params, key):
    values = params.get(key)
    if not values:
        return None
    if len(values) != 1:
        raise APIError(400, "invalid_parameter", f"{key} may be specified once")
    return values[0]
