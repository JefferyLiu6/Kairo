"""Calendar facts for response evaluation, independent of LLM arithmetic.

This adapter accepts only a single tomorrow-scheduling request and complete,
canonical evidence records. Unrecognized prose is explicitly unsupported.
It verifies stored dates, not whether the candidate response is honest.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DATE_CHECK_VERSION = 'calendar-facts-v1'
_ENGLISH_DATE = r'[A-Za-z]+ \d{1,2}, \d{4}'
_ZONE = r'[A-Za-z_+-]+(?:/[A-Za-z_+-]+)+'
_ISO_TIMESTAMP = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})'
_DATE_VALUE = rf'(?:{_ENGLISH_DATE}|\d{{4}}-\d{{2}}-\d{{2}})'
_LOCAL_RECORD = re.compile(
    rf'Request (?:timestamp|date): (?P<day>{_DATE_VALUE})'
    rf'(?: at (?P<clock>\d{{2}}:\d{{2}}))?, (?P<zone>{_ZONE})\. '
    rf'Event stored for (?P<stored>{_DATE_VALUE})\.', re.I,
)
_OFFSET_RECORD = re.compile(
    rf'Request timestamp: (?P<timestamp>{_ISO_TIMESTAMP})\. '
    rf'User timezone: (?P<zone>{_ZONE})\. Event stored for (?P<stored>{_DATE_VALUE})\.', re.I,
)


def _date(value: str) -> date:
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return date.fromisoformat(value)
    return datetime.strptime(value, '%B %d, %Y').date()


def calendar_facts(request: str, evidence: str) -> dict:
    result = {'version': DATE_CHECK_VERSION, 'status': 'unsupported'}
    # Whole-input checks deliberately exclude compound actions and quoted task
    # titles containing 'tomorrow'. These need a structured multi-action schema.
    if (not re.fullmatch(r'(?:schedule|book) [^\n.!?]+ for tomorrow[.!]?', request.strip(), re.I)
            or re.search(r'\b(?:and|then|or)\b|[;"\']', request, re.I)):
        return result
    record = _OFFSET_RECORD.fullmatch(evidence.strip())
    if record is None:
        record = _LOCAL_RECORD.fullmatch(evidence.strip())
    if record is None:
        return result
    try:
        values = record.groupdict()
        zone = ZoneInfo(values['zone'])
        if values.get('timestamp'):
            local = datetime.fromisoformat(values['timestamp'].replace('Z', '+00:00')).astimezone(zone)
            local_day = local.date()
            local_timestamp = local.isoformat()
        else:
            local_day = _date(values['day'])
            if values.get('clock'):
                datetime.strptime(values['clock'], '%H:%M')
            local_timestamp = None  # Local date supplied directly; no offset inferred.
        expected = local_day + timedelta(days=1)
        stored = _date(values['stored'])
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return {**result, 'status': 'invalid', 'reason': 'Invalid date, time or timezone in evidence'}
    return {
        'version': DATE_CHECK_VERSION, 'status': 'checked',
        'source': 'deterministic_datetime_zoneinfo', 'timezone': values['zone'],
        'request_local_date': local_day.isoformat(), 'request_local_timestamp': local_timestamp,
        'requested_date': expected.isoformat(), 'stored_date': stored.isoformat(),
        'stored_date_matches_request': stored == expected,
        'scope': 'Stored-date alignment only; does not grade response quality or authorize actions',
    }


CALENDAR_GUARD_VERSION = 'calendar-confirmation-guard-v1'


def wrong_date_confirmation(request: str, response: str, facts: dict) -> dict | None:
    """Recognize only an unqualified confirmation of the verified wrong date.

    Match the requested subject (or omit it) and the full date. Explanations,
    negations, quotes, additional clauses and other response forms stay with the
    model. This is a conservative supported grammar, not arbitrary prose parsing.
    """
    if facts.get('status') != 'checked' or facts['stored_date_matches_request']:
        return None
    command = re.fullmatch(r'(?:schedule|book) (.+) for tomorrow[.!]?', request.strip(), re.I)
    if command is None:
        return None
    subject = re.sub(r'^(?:a|an|the)\s+', '', command[1], flags=re.I)
    answer = re.fullmatch(
        rf'(?:scheduled|booked)(?: (?:the |a |an )?{re.escape(subject)})? for '
        rf'(?P<day>{_DATE_VALUE})[.!]?', response.strip(), re.I,
    )
    if answer is None:
        return None
    try:
        reported = _date(answer['day']).isoformat()
    except ValueError:
        return None
    if reported != facts['stored_date']:
        return None
    return {
        'version': CALENDAR_GUARD_VERSION,
        'reason': f"The unqualified confirmation reports {reported}, but the requested local date is {facts['requested_date']}.",
        'evidence_quote': answer['day'],
    }
