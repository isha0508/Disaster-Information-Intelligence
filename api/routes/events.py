from api.schemas import APIError, parse_bool, parse_page, response, validate_time


def list_events(repository, params):
    limit, offset = parse_page(params)
    since, until = validate_time(params, "since"), validate_time(params, "until")
    if since and until and since > until:
        raise APIError(400, "invalid_parameter", "since must not be later than until")
    include_history = parse_bool(params, "include_history")
    items, total = repository.query_events(source=_one(params, "source"),
        disaster_type=_one(params, "disaster_type"), operational_type=_one(params, "operational_type"),
        severity=_one(params, "severity"), priority=_one(params, "priority"),
        location=_one(params, "location"), since=since, until=until, limit=limit, offset=offset,
        include_history=True if include_history is None else include_history)
    return response(items, total=total, limit=limit, offset=offset)


def event_detail(repository, event_id):
    item = repository.get_event(event_id)
    if item is None:
        raise APIError(404, "not_found", "event not found")
    return response(item=item)


def _one(params, key):
    values = params.get(key)
    if not values:
        return None
    if len(values) != 1:
        raise APIError(400, "invalid_parameter", f"{key} may be specified once")
    return values[0]
