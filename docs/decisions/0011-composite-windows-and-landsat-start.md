# 0011 — Proposed composite windows and first Landsat year

- **Status:** accepted by the owner on 2026-11-02 (see Owner decision below)
- **Date:** 2026-11-02 (revised after science review loop 1)
- **Task:** M1.1

## Context

M1.2 needs composite windows for Sentinel-2 and M1.5 needs the first year the Landsat record supports annual
composites. CLAUDE.md says both come from the availability audit, not from assumptions. The audit (ADR 0010; AOI of
ADR 0009; run date 2026-11-02, item search frozen in `outputs/M1.1/cache/`) gives, per window and year, the share of AOI
pixels with zero clear observations, the median and lower tail of the per-pixel count of clear observations and the
number of acquisitions with data. This ADR presents those tables, states the rule used to read them as an explicit
assumption, and proposes windows and a year. Nothing here is accepted until the owner says so, and M1.1 stays unticked
until then.

## Owner decision (2026-11-02)

- **Sentinel-2 composite windows for M1.2:** all four Met Department seasons — FIM (Mar–Apr), SWM (May–Sep),
  SIM (Oct–Nov) and NEM (Dec–Feb, December counted in the following year). The thin season-years listed below
  (SIM in most years; FIM and NEM 2016; FIM 2017) are kept but must be flagged in M1.2 outputs, because a median
  over 1–2 clear observations cannot reject undetected cloud.
- **First Landsat year for M1.5: 2000** (not 1999, which rests on a single scene). The gap-tolerant option using
  1994–1997 is not adopted now; revisit it if the start-of-period forest mask (M1.4/M1.6) needs earlier years.

The proposal text below is kept as the record of how these were derived.

## Decision (as proposed)

### Assumptions (the rule)

- **A1 Candidate windows** are the four Met Department seasons from CLAUDE.md: FIM Mar–Apr, SWM May–Sep, SIM Oct–Nov, NEM
  Dec–Feb (December counts toward the following year). No other splits were tested.
- **A2 Minimum for a window** is one clear observation for the median AOI pixel in each complete season-year. This is a
  choice, not a result, and it has two consequences the owner should weigh:
  (a) a season-year passes even if up to 49.9 % of its pixels have zero clear observations (the median pixel is what
  counts), and
  (b) with 1–2 observations per pixel a median rejects no outliers, so cloud edges that SCL misses pass straight into a
  composite. The science review found, on 2024-10-23 (a SIM date), 24 % of SCL-clear pixels with NDVI ≤ 0.5 against 10–11 %
  on clean dates (review evidence, not recomputed in this audit).
  No percentage cut-off is applied, because none can be derived from this data. To make the thin cells visible the tables
  below add the lower tail of the per-pixel count (share of pixels with ≤ 1 and ≤ 2 observations, and the 10th percentile).
- **A3 Evaluation years** for Sentinel-2 are 2016–2025 plus 2026 where the season is complete, because the first S2
  acquisition with data over the AOI is 2016-01-14 (2015 has none, see ADR 0010). SIM 2026 is partial (the run date is
  2026-11-02) and is excluded.
- **A4 First Landsat year** is the first calendar year Y such that every year from Y to the last complete year (2025)
  has at least one clear observation for the median pixel (a gap-free, contiguous series).
- **A5** The clear definition is `clear_base` for Sentinel-2 (SCL 4, 5, 6) and `clear` for Landsat (QA_PIXEL bits 0, 1, 3,
  4, 5, plus cirrus for L8/9). Choosing the SCL keep-set is M1.2's decision (ADR 0010); `clear_keep27` is shown to bound its
  effect.

### Proposal 1 — Sentinel-2 windows for M1.2

Use all four Met Department seasons as windows. Under A2 every window passes in every evaluated season-year: the minimum of
the yearly median counts is 1 (FIM, NEM), 3 (SWM) and 2 (SIM). The proposal does not change after the review.

**Per-window totals are not a ranking of seasons.** SWM spans five months and the others two or three, so its per-pixel
totals are larger for that reason alone. The like-for-like measure is the clear fraction of a single acquisition
(acquisitions with data over the AOI, `clear_base`, all years 2016–2026):

| window | months | acquisitions | mean clear fraction | median clear fraction |
|---|---|---|---|---|
| FIM Mar–Apr | 2 | 115 | 0.518 | 0.515 |
| SWM May–Sep | 5 | 301 | 0.360 | 0.177 |
| SIM Oct–Nov | 2 | 113 | 0.258 | 0.090 |
| NEM Dec–Feb | 3 | 163 | 0.382 | 0.274 |

By calendar month (mean / median clear fraction, n acquisitions): Jan 0.356 / 0.076 (57); Feb 0.499 / 0.443 (53); Mar 0.585 /
0.544 (57); Apr 0.453 / 0.406 (58); May 0.309 / 0.070 (63); Jun 0.348 / 0.163 (56); Jul 0.441 / 0.352 (64); Aug 0.392 /
0.229 (62); Sep 0.303 / 0.087 (56); Oct 0.266 / 0.144 (60); Nov 0.249 / 0.073 (53); Dec 0.293 / 0.245 (53). Per acquisition, FIM
is the clearest window and SIM the least clear; SWM and NEM sit between. The data support "SIM is the thinnest" and nothing
firmer about the ranking of the other three. Landsat has the same extremes (FIM clearest, SIM least clear; by month Mar 0.576, Jul 0.523, Oct 0.259, Nov
0.214, Dec 0.248 mean clear fraction; windows FIM 0.487, SWM 0.431, NEM 0.337, SIM 0.238).

A 5-month SWM median spans May–Sep phenology, so a single SWM composite mixes dates in a way a 2-month window does not;
M1.2 should record the observation dates (or day-of-year) used per pixel.

Season-years that are thin in the tables below: FIM 2016 and 2017, NEM 2016, SIM 2019, 2023 and 2026 (and SIM 2020, FIM
2022). Whether a median of 1–2 observations is robust enough, and whether to drop 2016–2017 (single-satellite years: 18 and
26 valid acquisitions), is left to the owner; M1.2 should carry the per-pixel observation count with each composite so that
low-count pixels are flagged and not silently filled.

Each cell is `% of AOI pixels with zero clear observations / median clear observations per pixel / acquisitions with data
(valid_frac > 0; the seven no-data acquisitions of ADR 0010 are excluded)`; `*` marks a partial window. Sentinel-2,
`clear_base`:

| season_year | FIM_Mar-Apr | SWM_May-Sep | SIM_Oct-Nov | NEM_Dec-Feb |
|---|---|---|---|---|
| 2016 | 5.9% / 1 / 2 | 0.2% / 3 / 8 | 0.1% / 2 / 4 | 2.0% / 1 / 2 |
| 2017 | 19.8% / 1 / 3 | 0.0% / 6 / 10 | 2.6% / 2 / 7 | 0.2% / 2 / 4 |
| 2018 | 0.3% / 5 / 11 | 0.1% / 8 / 28 | 0.7% / 4 / 12 | 0.1% / 8 / 14 |
| 2019 | 0.0% / 8 / 12 | 0.1% / 12 / 29 | 1.4% / 2 / 12 | 0.1% / 8 / 18 |
| 2020 | 0.1% / 9 / 12 | 0.1% / 6 / 30 | 6.1% / 3 / 12 | 0.1% / 5 / 18 |
| 2021 | 0.2% / 7 / 12 | 0.0% / 10 / 29 | 0.1% / 3 / 8 | 0.1% / 6 / 17 |
| 2022 | 4.4% / 3 / 12 | 0.0% / 13 / 31 | 0.8% / 4 / 11 | 0.0% / 8 / 17 |
| 2023 | 0.1% / 6 / 12 | 0.0% / 11 / 30 | 7.8% / 2 / 12 | 0.0% / 3 / 17 |
| 2024 | 0.0% / 6 / 12 | 0.0% / 11 / 29 | 1.0% / 3 / 12 | 0.0% / 7 / 18 |
| 2025 | 0.4% / 4 / 14 | 0.0% / 14 / 39 | 0.4% / 3 / 15 | 0.1% / 6 / 18 |
| 2026 | 0.0% / 9 / 13 | 0.0% / 15 / 38 | 12.9% / 2 / 8* | 0.0% / 6 / 20 |

Lower tail of the per-pixel count (`% of pixels with ≤ 1 observation / % with ≤ 2 / 10th percentile of the count`):

| season_year | FIM_Mar-Apr | SWM_May-Sep | SIM_Oct-Nov | NEM_Dec-Feb |
|---|---|---|---|---|
| 2016 | 86.7% / 100.0% / 1 | 4.0% / 43.8% / 2 | 36.3% / 90.7% / 1 | 68.2% / 100.0% / 1 |
| 2017 | 66.2% / 92.6% / 0 | 0.1% / 0.3% / 5 | 19.1% / 72.4% / 1 | 2.6% / 64.5% / 2 |
| 2018 | 1.2% / 2.4% / 4 | 0.1% / 0.2% / 6 | 3.5% / 14.9% / 2 | 0.1% / 0.3% / 7 |
| 2019 | 0.1% / 0.1% / 7 | 0.1% / 0.1% / 9 | 36.7% / 85.5% / 1 | 0.1% / 0.2% / 7 |
| 2020 | 0.1% / 0.3% / 7 | 0.2% / 0.6% / 4 | 23.0% / 47.6% / 1 | 0.2% / 1.0% / 3 |
| 2021 | 0.6% / 1.2% / 6 | 0.1% / 0.1% / 8 | 9.9% / 49.8% / 2 | 0.6% / 1.3% / 4 |
| 2022 | 14.4% / 30.5% / 1 | 0.0% / 0.0% / 10 | 4.7% / 9.2% / 3 | 0.1% / 0.1% / 6 |
| 2023 | 0.1% / 0.2% / 4 | 0.0% / 0.1% / 9 | 28.4% / 62.7% / 1 | 0.1% / 21.7% / 2 |
| 2024 | 0.1% / 0.5% / 5 | 0.0% / 0.1% / 8 | 3.6% / 32.4% / 2 | 0.0% / 0.1% / 6 |
| 2025 | 4.9% / 15.4% / 2 | 0.0% / 0.0% / 11 | 5.0% / 31.9% / 2 | 0.1% / 0.4% / 4 |
| 2026 | 0.1% / 0.1% / 7 | 0.0% / 0.0% / 13 | 30.3% / 65.5% / 0* | 0.1% / 0.5% / 4 |

Across the 10–11 complete season-years from 2016 (`clear_base`):

| window | complete years | median of zero-obs share | max zero-obs share | median of yearly medians | min / max of yearly medians |
|---|---|---|---|---|---|
| FIM Mar–Apr | 11 | 0.16 % | 19.8 % | 6 | 1 / 9 |
| SWM May–Sep | 11 | 0.04 % | 0.23 % | 11 | 3 / 15 |
| SIM Oct–Nov | 10 | 0.90 % | 7.8 % | 3 | 2 / 4 |
| NEM Dec–Feb | 11 | 0.06 % | 2.0 % | 6 | 1 / 8 |

With `clear_keep27` (classes 2 and 7 kept as clear) the zero-observation shares fall slightly and no window changes
status: FIM 2016 4.7 %, FIM 2017 14.8 %, FIM 2022 4.1 %, SIM 2020 3.3 %, SIM 2023 6.2 %, SIM 2026 (partial) 12.3 %
(full table: `outputs/M1.1/window_summary_s2.csv`, regenerated by the notebook).

Lower tails show what the zero-observation share hides. SIM has 3.5–36.7 % of pixels with ≤ 1 observation in every year from
2018 and 47.6–85.5 % with ≤ 2 in 2019–2021, 2023 and 2026 (partial); SWM has at most 4.0 % with ≤ 1 from 2016 and
0.2 % or less from 2017. The p10 of the SIM count is 0–3, of the SWM count 2–13.

### Proposal 2 — first Landsat year for M1.5

**1999** under A4: 1998 has no acquisition at all, and every year from 1999 to 2025 has a median of at least 1 clear
observation per pixel. Two cautions go with it, and the owner may prefer **2000** (the first year with more than one
acquisition, 6 of them, and with 100 % of pixels observed):

- 1999 rests on a single Landsat 7 scene (98.88 % of pixels have one clear observation).
- From 2000 on every year has at least 99.87 % of pixels with a clear observation (worst: 2008 at 99.88 %, 2020 at 99.95 %,
  2002 at 99.99 %).
- Before 1999 the record is discontinuous (Landsat 5 only): 1988 13 scenes, 1989 1, 1990 none, 1991 4, 1992 6, 1993 4,
  1994–1997 12–20, 1998 none. 1989, 1990 and 1998 have no usable year and 1991 and 1993 have 86.3 % and 81.6 % of pixels
  observed. Nothing is in the PC archive for this path/row before 1988-01-14. The science review found the 1989/1990/1998
  gaps identical on AWS Earth Search, so they are a USGS archive limit and not a Planetary Computer one (not re-checked in
  this run).
- **Gap-tolerant option for the owner.** A4 requires a gap-free series and so discards 1994–1997, four good Landsat 5 years
  (12–20 scenes each, 100 % of pixels observed, median 4–9 clear observations). A gap-tolerant rule (use any year with
  adequate coverage, accept missing years) would extend the record to 1988/1992/1994–1997. That matters for the
  start-of-period forest mask (M1.4/M1.6), which needs early-period NBR. The choice is the owner's.
- Sensor changes inside the proposed series: Landsat 5 and 7 both contribute in 2001 and 2003–2011, Landsat 7 is the sole
  sensor in 1999–2000, 2002 and 2012 (2012 is SLC-off only, 16 scenes), Landsat 7 and 8 overlap in 2013, Landsat 8 is alone
  2014–2020, and Landsat 9 joins in 2021. Harmonisation in M1.5 must cover TM, ETM+ (including SLC-off after 2003) and
  OLI/OLI-2.
- The S2 20 m and Landsat 30 m grids have origins that do not align (15 m offset), so any cross-sensor comparison needs an
  explicit regrid.
- **Season mix varies by year.** Annual composites draw on different parts of the year in different years: for example
  2003 has 57 % of pixels with zero SWM observations and 100 % with zero SIM; SIM 2010 82 %; SWM 2011 74 %; NEM 2020 95 %.
  M1.5 must carry the observation day-of-year or season with each annual composite, because phenology differences can look
  like change.

Landsat, `clear`, per calendar year (percentages rounded to two decimals; 100.0 may be 99.995 or above):

| year | acq with data | platforms L5/L7/L8/L9 | % pixels >=1 clear | median clear/pixel | % >=1 (cirrus kept) | partial |
|---|---|---|---|---|---|---|
| 1988 | 13 | 13/0/0/0 | 100.0 | 6.0 | 100.0 | False |
| 1989 | 1 | 1/0/0/0 | 0.0 | 0.0 | 0.0 | False |
| 1990 | 0 | 0/0/0/0 | 0.0 | 0.0 | 0.0 | False |
| 1991 | 4 | 4/0/0/0 | 86.31 | 1.0 | 86.31 | False |
| 1992 | 6 | 6/0/0/0 | 100.0 | 4.0 | 100.0 | False |
| 1993 | 4 | 4/0/0/0 | 81.59 | 1.0 | 81.59 | False |
| 1994 | 18 | 18/0/0/0 | 100.0 | 7.0 | 100.0 | False |
| 1995 | 12 | 12/0/0/0 | 100.0 | 4.0 | 100.0 | False |
| 1996 | 20 | 20/0/0/0 | 100.0 | 7.0 | 100.0 | False |
| 1997 | 14 | 14/0/0/0 | 100.0 | 9.0 | 100.0 | False |
| 1998 | 0 | 0/0/0/0 | 0.0 | 0.0 | 0.0 | False |
| 1999 | 1 | 0/1/0/0 | 98.88 | 1.0 | 98.88 | False |
| 2000 | 6 | 0/6/0/0 | 100.0 | 4.0 | 100.0 | False |
| 2001 | 13 | 2/11/0/0 | 100.0 | 5.0 | 100.0 | False |
| 2002 | 18 | 0/18/0/0 | 99.99 | 10.0 | 99.99 | False |
| 2003 | 12 | 2/10/0/0 | 100.0 | 4.0 | 100.0 | False |
| 2004 | 33 | 20/13/0/0 | 100.0 | 13.0 | 100.0 | False |
| 2005 | 24 | 14/10/0/0 | 100.0 | 8.0 | 100.0 | False |
| 2006 | 33 | 20/13/0/0 | 100.0 | 13.0 | 100.0 | False |
| 2007 | 27 | 15/12/0/0 | 100.0 | 13.0 | 100.0 | False |
| 2008 | 28 | 18/10/0/0 | 99.88 | 10.0 | 99.88 | False |
| 2009 | 34 | 21/13/0/0 | 100.0 | 15.0 | 100.0 | False |
| 2010 | 16 | 6/10/0/0 | 100.0 | 6.0 | 100.0 | False |
| 2011 | 15 | 5/10/0/0 | 100.0 | 6.0 | 100.0 | False |
| 2012 | 16 | 0/16/0/0 | 100.0 | 6.0 | 100.0 | False |
| 2013 | 25 | 0/12/13/0 | 100.0 | 8.0 | 100.0 | False |
| 2014 | 23 | 0/0/23/0 | 100.0 | 10.0 | 100.0 | False |
| 2015 | 23 | 0/0/23/0 | 100.0 | 8.0 | 100.0 | False |
| 2016 | 23 | 0/0/23/0 | 100.0 | 12.0 | 100.0 | False |
| 2017 | 23 | 0/0/23/0 | 100.0 | 9.0 | 100.0 | False |
| 2018 | 22 | 0/0/22/0 | 100.0 | 8.0 | 100.0 | False |
| 2019 | 22 | 0/0/22/0 | 100.0 | 9.0 | 100.0 | False |
| 2020 | 22 | 0/0/22/0 | 99.95 | 7.0 | 99.95 | False |
| 2021 | 23 | 0/0/22/1 | 100.0 | 11.0 | 100.0 | False |
| 2022 | 40 | 0/0/19/21 | 100.0 | 11.0 | 100.0 | False |
| 2023 | 42 | 0/0/21/21 | 100.0 | 13.0 | 100.0 | False |
| 2024 | 44 | 0/0/22/22 | 100.0 | 18.0 | 100.0 | False |
| 2025 | 44 | 0/0/22/22 | 100.0 | 13.0 | 100.0 | False |
| 2026 | 38 | 0/0/19/19 | 100.0 | 16.0 | 100.0 | True |

Landsat per season from 1999, `clear`, same cell format as above (zero share / median / acquisitions with data):

| season_year | FIM_Mar-Apr | SWM_May-Sep | SIM_Oct-Nov | NEM_Dec-Feb |
|---|---|---|---|---|
| 1999 | 100.0% / 0 / 0 | 100.0% / 0 / 0 | 1.1% / 1 / 1 | 100.0% / 0 / 0 |
| 2000 | 1.2% / 1 / 1 | 13.2% / 1 / 1 | 6.3% / 1 / 2 | 0.0% / 1 / 1 |
| 2001 | 0.0% / 1 / 2 | 0.0% / 3 / 5 | 99.7% / 0 / 2 | 0.0% / 1 / 3 |
| 2002 | 2.0% / 1 / 4 | 0.1% / 3 / 7 | 4.6% / 3 / 4 | 0.5% / 1 / 4 |
| 2003 | 1.9% / 1 / 1 | 57.3% / 0 / 3 | 100.0% / 0 / 1 | 2.5% / 2 / 4 |
| 2004 | 1.3% / 3 / 6 | 0.0% / 8 / 15 | 64.0% / 0 / 6 | 0.2% / 3 / 8 |
| 2005 | 0.0% / 2 / 6 | 0.0% / 4 / 9 | 100.0% / 0 / 2 | 9.2% / 1 / 7 |
| 2006 | 5.1% / 2 / 7 | 0.0% / 7 / 13 | 16.9% / 1 / 6 | 3.6% / 2 / 6 |
| 2007 | 0.6% / 3 / 5 | 0.0% / 5 / 9 | 10.5% / 2 / 5 | 0.1% / 3 / 10 |
| 2008 | 4.8% / 3 / 7 | 0.3% / 6 / 10 | 32.4% / 1 / 4 | 20.4% / 1 / 4 |
| 2009 | 0.0% / 3 / 5 | 0.0% / 6 / 13 | 0.0% / 1 / 6 | 0.0% / 5 / 11 |
| 2010 | 26.7% / 1 / 2 | 0.0% / 2 / 3 | 81.7% / 0 / 3 | 0.0% / 4 / 10 |
| 2011 | 1.5% / 2 / 4 | 73.8% / 0 / 1 | 12.4% / 2 / 4 | 8.1% / 2 / 6 |
| 2012 | 20.4% / 1 / 2 | 0.0% / 4 / 6 | 41.0% / 1 / 4 | 19.7% / 1 / 3 |
| 2013 | 44.5% / 1 / 2 | 0.0% / 4 / 11 | 11.0% / 2 / 7 | 17.1% / 2 / 5 |
| 2014 | 0.1% / 2 / 4 | 0.0% / 5 / 9 | 57.7% / 0 / 4 | 0.0% / 3 / 6 |
| 2015 | 9.9% / 2 / 4 | 3.8% / 4 / 9 | 51.6% / 0 / 4 | 4.5% / 1 / 6 |
| 2016 | 0.0% / 2 / 3 | 0.0% / 5 / 10 | 11.7% / 2 / 4 | 0.0% / 4 / 6 |
| 2017 | 0.0% / 3 / 4 | 0.0% / 4 / 10 | 47.4% / 1 / 4 | 4.3% / 2 / 5 |
| 2018 | 10.9% / 2 / 4 | 3.0% / 3 / 10 | 11.3% / 1 / 3 | 0.0% / 2 / 5 |
| 2019 | 0.0% / 3 / 4 | 0.0% / 5 / 9 | 75.4% / 0 / 4 | 0.0% / 2 / 6 |
| 2020 | 1.9% / 2 / 4 | 15.5% / 1 / 9 | 2.7% / 2 / 3 | 95.1% / 0 / 5 |
| 2021 | 0.8% / 2 / 4 | 0.0% / 4 / 9 | 30.4% / 1 / 3 | 1.1% / 3 / 6 |
| 2022 | 17.5% / 1 / 5 | 0.0% / 6 / 17 | 12.9% / 1 / 7 | 0.0% / 2 / 10 |
| 2023 | 0.0% / 3 / 6 | 0.0% / 6 / 18 | 38.7% / 1 / 8 | 1.3% / 4 / 11 |
| 2024 | 0.0% / 4 / 8 | 0.0% / 7 / 19 | 35.6% / 1 / 7 | 0.0% / 5 / 11 |
| 2025 | 1.8% / 2 / 7 | 0.1% / 7 / 19 | 2.3% / 2 / 7 | 0.0% / 4 / 10 |
| 2026 | 0.9% / 3 / 8 | 0.0% / 11 / 19 | 68.3% / 0 / 4* | 0.2% / 2 / 10 |

Landsat lower tail (`% ≤ 1 / % ≤ 2 / p10`):

| season_year | FIM_Mar-Apr | SWM_May-Sep | SIM_Oct-Nov | NEM_Dec-Feb |
|---|---|---|---|---|
| 1999 | 100.0% / 100.0% / 0 | 100.0% / 100.0% / 0 | 100.0% / 100.0% / 1 | 100.0% / 100.0% / 0 |
| 2000 | 100.0% / 100.0% / 1 | 100.0% / 100.0% / 0 | 100.0% / 100.0% / 1 | 100.0% / 100.0% / 1 |
| 2001 | 96.0% / 100.0% / 1 | 0.0% / 19.3% / 2 | 100.0% / 100.0% / 0 | 98.4% / 100.0% / 1 |
| 2002 | 58.5% / 98.5% / 1 | 2.5% / 25.5% / 2 | 12.6% / 22.7% / 1 | 59.6% / 94.1% / 1 |
| 2003 | 100.0% / 100.0% / 1 | 99.1% / 100.0% / 0 | 100.0% / 100.0% / 0 | 23.5% / 64.8% / 1 |
| 2004 | 8.5% / 33.5% / 2 | 0.0% / 0.0% / 6 | 94.6% / 99.8% / 0 | 5.2% / 24.5% / 2 |
| 2005 | 19.6% / 63.2% / 1 | 0.7% / 10.3% / 2 | 100.0% / 100.0% / 0 | 53.8% / 99.0% / 1 |
| 2006 | 24.9% / 56.0% / 1 | 0.0% / 0.1% / 5 | 67.2% / 98.3% / 0 | 24.3% / 51.2% / 1 |
| 2007 | 1.9% / 15.4% / 2 | 0.4% / 3.9% / 3 | 29.3% / 73.4% / 0 | 4.2% / 28.9% / 2 |
| 2008 | 10.9% / 38.5% / 1 | 2.1% / 5.3% / 3 | 82.5% / 100.0% / 0 | 91.8% / 100.0% / 0 |
| 2009 | 6.4% / 37.1% / 2 | 0.0% / 1.3% / 4 | 85.3% / 100.0% / 1 | 0.0% / 0.3% / 4 |
| 2010 | 93.7% / 100.0% / 0 | 5.5% / 100.0% / 2 | 98.8% / 100.0% / 0 | 0.3% / 13.6% / 2 |
| 2011 | 27.2% / 81.5% / 1 | 100.0% / 100.0% / 0 | 48.5% / 82.8% / 0 | 33.6% / 82.8% / 1 |
| 2012 | 83.5% / 100.0% / 0 | 0.6% / 15.9% / 2 | 98.8% / 100.0% / 0 | 88.1% / 99.9% / 0 |
| 2013 | 100.0% / 100.0% / 0 | 2.9% / 12.9% / 2 | 37.2% / 76.3% / 0 | 32.8% / 99.7% / 0 |
| 2014 | 14.8% / 68.6% / 1 | 0.0% / 5.9% / 3 | 93.3% / 100.0% / 0 | 0.0% / 11.7% / 2 |
| 2015 | 43.3% / 85.9% / 1 | 8.7% / 23.5% / 2 | 90.4% / 99.9% / 0 | 82.6% / 99.6% / 1 |
| 2016 | 23.4% / 91.3% / 1 | 0.0% / 2.1% / 3 | 44.7% / 89.2% / 0 | 0.0% / 0.5% / 3 |
| 2017 | 1.0% / 30.8% / 2 | 0.4% / 7.3% / 3 | 73.8% / 94.6% / 0 | 38.5% / 98.5% / 1 |
| 2018 | 26.2% / 55.1% / 0 | 13.3% / 41.7% / 1 | 83.0% / 100.0% / 0 | 47.3% / 91.2% / 1 |
| 2019 | 3.2% / 22.7% / 2 | 0.0% / 3.7% / 3 | 100.0% / 100.0% / 0 | 18.9% / 95.8% / 1 |
| 2020 | 13.2% / 69.2% / 1 | 63.3% / 94.8% / 0 | 15.2% / 56.0% / 1 | 99.9% / 100.0% / 0 |
| 2021 | 14.2% / 51.8% / 1 | 0.0% / 5.6% / 3 | 88.8% / 100.0% / 0 | 7.0% / 22.4% / 2 |
| 2022 | 51.9% / 88.8% / 0 | 0.4% / 2.0% / 4 | 87.2% / 99.8% / 0 | 25.7% / 71.3% / 1 |
| 2023 | 5.4% / 19.6% / 2 | 0.8% / 3.0% / 4 | 67.6% / 92.0% / 0 | 4.3% / 13.2% / 2 |
| 2024 | 1.5% / 7.2% / 3 | 0.0% / 0.0% / 5 | 72.2% / 90.8% / 0 | 0.0% / 0.0% / 4 |
| 2025 | 35.7% / 79.7% / 1 | 0.3% / 1.1% / 4 | 18.6% / 78.8% / 1 | 0.0% / 0.0% / 4 |
| 2026 | 11.7% / 33.1% / 1 | 0.0% / 0.0% / 8 | 96.6% / 100.0% / 0* | 18.9% / 61.4% / 1 |

The Landsat seasonal picture is much thinner than Sentinel-2's: SIM (Oct–Nov) has a median of 0–3 observations and
zero-observation shares above 50 % in several years, so seasonal Landsat composites for SIM would not support a median.
Annual composites (the M1.5 plan) are supported; a seasonal Landsat composite is not proposed.

## Consequences

- If accepted, M1.2 builds four composites per year from 2016 (SIM 2026 once complete), and M1.5 starts annual Landsat
  composites at 1999 (or 2000).
- Rejecting any assumption (for example requiring a higher minimum count) changes the proposal; the per-pixel count rasters
  (`outputs/M1.1/clear_counts_*`) and the notebook let the tables be recomputed without re-reading any scene.
- Cloud-clear status here is whatever SCL and QA_PIXEL call clear. Neither is validated against ground truth, so
  composites may still contain thin cloud or shadow, and with 1–2 observations nothing is averaged out.
- Terrain shading is not modelled here; M1.3 supplies the per-scene shadow flag.
