"""Backward-compat stub — all logic has moved to its final home."""
# ruff: noqa
from ..calendar.service import is_google_calendar_connected as _google_calendar_connected  # noqa: F401
from ..executors.schedule import (  # noqa: F401
    _find_google_schedule_conflicts,
    _find_schedule_conflicts,
    _format_google_schedule_add_reply,
    _format_google_schedule_list,
    _format_schedule_list,
    _google_patch_from_update,
    _schedule_entries_can_write_google,
    _write_schedule_entries_to_google,
)
from ..resolvers.schedule import (  # noqa: F401
    _google_delete_targets_for_events,
    _google_event_ref,
    _google_schedule_target,
    _hide_google_mirror_refs,
    _local_schedule_target,
    _matching_google_schedule_refs,
    _resolve_schedule_refs,
    _split_calendar_ref_when,
    resolve_schedule_targets,
)
