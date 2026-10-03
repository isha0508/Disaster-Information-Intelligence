from api.schemas import response


def summary(repository):
    return response(**repository.intelligence_summary())
