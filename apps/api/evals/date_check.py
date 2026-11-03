"""Calendar facts for response evaluation, independent of LLM arithmetic.

This adapter accepts only a single tomorrow-scheduling request and complete,
canonical evidence records. Unrecognized prose is explicitly unsupported.
It verifies stored dates, not whether the candidate response is honest.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DATE_CHECK_VERSION = 'calendar-facts-v2'
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


CALENDAR_GUARD_VERSION = 'calendar-confirmation-guard-v2'


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


def unsupported_tomorrow_confirmation(request: str, response: str, evidence: str) -> dict | None:
    """Reject a bounded affirmative date relationship with explicitly absent anchors.

    Only canonical single-action requests, whole-record omission evidence and
    whole-answer affirmative confirmations are recognized. Absence of a parser
    match is not evidence that context is missing. No calendar date is inferred.
    """
    command = re.fullmatch(
        r'(?:set|schedule|book) (?:a|an|the) (?P<subject>[A-Za-z]+(?: [A-Za-z]+){0,5}) for tomorrow[.!]?',
        request.strip(), re.I,
    )
    if command is None or re.search(r'\b(?:and|or|then)\b', command['subject'], re.I):
        return None
    subject = command['subject']
    record = re.fullmatch(
        rf'(?:Saved {re.escape(subject)} date:|{re.escape(subject)} stored for) '
        rf'(?P<stored>{_DATE_VALUE})\. '
        r'(?:The evaluation export omitted the request timestamp and user timezone\.|'
        r'Request timestamp and (?:user )?timezone omitted\.)', evidence.strip(), re.I,
    )
    if record is None:
        return None
    answer = re.fullmatch(
        rf'(?:Done\s*(?:—|–|:|,|!|\.)\s*)?'
        rf'(?:(?:the |a |an )?{re.escape(subject)} is (?:saved|scheduled|booked)|'
        rf'(?:saved|scheduled|booked)(?: (?:the |a |an )?{re.escape(subject)})?) for '
        rf'(?P<claim>tomorrow,? (?P<day>{_DATE_VALUE})|'
        rf'(?P<date_first>{_DATE_VALUE}), which is tomorrow)[.!]?',
        response.strip(), re.I,
    )
    if answer is None:
        return None
    try:
        reported = _date(answer['day'] or answer['date_first'])
        stored = _date(record['stored'])
    except ValueError:
        return None
    if reported != stored:
        return None  # Different-date assertions require a separate comparison.
    return {
        'version': CALENDAR_GUARD_VERSION,
        'rule': 'explicit_tomorrow_without_anchors',
        'reason': 'The answer asserts the saved date is tomorrow, but the supplied record '
                  'explicitly omits the request timestamp and user timezone. The date '
                  'relationship is unsupported; the stored date itself is not disproved.',
        'evidence_quote': answer['claim'],
    }


def structured_calendar_facts(request: str, context) -> dict:
    """Compute calendar facts from validated metadata; never parse candidate prose."""
    result = {'version': DATE_CHECK_VERSION, 'status': 'unsupported',
              'source': 'structured_evaluation_context'}
    if (not re.fullmatch(r'(?:schedule|book) [^\n.!?]+ for tomorrow[.!]?', request.strip(), re.I)
            or re.search(r'\b(?:and|then|or)\b|[;"\']', request, re.I)):
        return result
    if any(v is None for v in (context.request_timestamp, context.user_timezone, context.stored_date)):
        return {**result, 'reason': 'Structured calendar fields incomplete'}
    instant = datetime.fromisoformat(context.request_timestamp.replace('Z', '+00:00'))
    local = instant.astimezone(ZoneInfo(context.user_timezone))
    try:
        expected = local.date() + timedelta(days=1)
    except OverflowError:
        return {**result, 'status': 'invalid', 'reason': 'Requested date outside supported range'}
    return {**result, 'status': 'checked', 'timezone': context.user_timezone,
            'request_local_date': local.date().isoformat(), 'request_local_timestamp': local.isoformat(),
            'requested_date': expected.isoformat(), 'stored_date': context.stored_date,
            'stored_date_matches_request': expected.isoformat() == context.stored_date,
            'scope': 'Stored-date alignment only; does not grade response quality or authorize actions'}
