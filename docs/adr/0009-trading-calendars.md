# ADR 0009 — Trading calendars

- Status: accepted
- Date: 2026-10-19

## Context

Quality checks (missing days), fetch planning and later backtests need each venue's trading
days, including Turkish religious holidays that move every year and their half days.

## Decision

1. Use `exchange-calendars` (4.13.2) through `qp.reference.calendars`; no other module
   imports it. Venue map: BIST and TEFAS → XIST, NYSE and ARCA → XNYS, NASDAQ → XNAS,
   BINANCE → 24/7, TCMB → XIST without half days.
2. Verified on 2026-10-19 for XIST 2026: Ramazan Bayramı (20–22 Mar) and Kurban Bayramı
   (27–30 May) are holidays; 19 Mar, 26 May and 28 Oct are half days (close 12:30 Istanbul).
3. A date outside the library's range raises `CalendarError`; nothing is extrapolated.

## Consequences

- Future holidays are those known to the installed library version, not point in time. A
  newly announced holiday arrives with a library upgrade; checks for past dates are
  unaffected.
- Upgrading the library is a reviewed change: the calendar tests pin known 2026 dates.
