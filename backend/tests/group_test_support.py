"""Explicit premises shared by authored group generative-stage tests."""

from enloq.domain.group_filtering import AD_ROLE


def known_non_ads(app):
    """Seed exact non-ad outcomes so a test isolates its generative stage.

    Requests still use the real classifier evidence projection and reuse rules.
    Dedicated filtering tests exercise actual decision dispatch and accounting;
    these fixtures state that their authored discussion is already classified.
    """
    filtering = app.briefings.filtering
    plan = filtering.plan

    def prepared(key, sources, day, **options):
        requests = plan(key, sources, day, **options)
        pending = []
        for request in requests:
            if request["role"] != AD_ROLE:
                pending.append(request)
                continue
            filtering.storage.record(
                request,
                {
                    f"ad_{source.rsplit('/', 1)[1]}": 0.0
                    for source in request["target_keys"]
                },
            )
        return pending

    filtering.plan = prepared


def briefing_output(ids, *, text="The authored participants reported progress."):
    """Current provider contract with one fully cited authored topic."""
    return dict(
        contract_version=2,
        outcome="briefing",
        topics=[
            dict(
                title="Progress",
                text=text,
                citations=list(ids),
                participant_source_ids=[next(iter(ids))],
            )
        ],
    )
