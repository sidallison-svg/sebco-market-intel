# Walkthrough notes — May 19, 2026

## Trends page
- [ ] Legend labels overlap with hover tooltip — submarket names get covered when hovering a data point.
- [ ] Empty chart space: "All time" on OC clusters almost everything at Mar 2026 with stray points before. Default range should probably be tighter (last 12 mo?) or older data needs review.
- [ ] Legend is huge — 15+ submarkets shown individually with (n=1). Ask Shawn if they want all at once or filter to a few.

## Upload page — duplicate detection bug
- [ ] Order-of-operations issue: PDF saves to DB FIRST, then duplicate check fires and shows "already uploaded" error. User sees error and assumes upload failed, but data is already in. Fix: move duplicate check BEFORE the save.
- [ ] Possible duplicate records in DB from this bug — check uploads table for repeated file_hash/filename once fix is in.

## Feature request — new construction filter
- [ ] Sebco cares about new construction. Add filter/view for it.
- Open question: do current parsers capture this? Check market_data schema.
- If no: parser extension project — Kidder/CBRE/Voit/JLL all have construction sections.
- Ask Shawn/Gabe: WHAT about new construction matters most? Pipeline sf? Deliveries? Pre-leasing rates? Which submarkets?

## UPDATE — construction data already exists in DB
Confirmed metrics present: deliveries, ytd_deliveries, under_construction, planned_construction, preleased_pct.
This is a UI task, not a parser extension. Roughly an afternoon of work.
Revised question for Shawn: "We already track these 5 construction metrics — which do you actually use?"

## By-source construction breakdown
- Kidder (OC, SD): planned_construction + under_construction. Strongest forward-looking coverage.
- JLL OC: full picture (deliveries, under_construction, ytd_deliveries).
- CBRE OC: deliveries + under_construction, but only 3-4 records.
- JLL Seattle/LA: market-level only, 1 record each per metric.
- Voit (OC, SD): ZERO construction metrics. Check if Voit reports include construction tables or if parser skips them.
- Marysville, Kent Valley, LA submarkets: no reports at all → no data of any kind.

Meeting question to add: "Construction is best for OC and SD. For the other 4 markets, do you have report sources I should add?"
