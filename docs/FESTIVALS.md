# Festivals (dashboard → Festivals)

India's festivals, vrats, jayantis and national days from November 2026 to December 2027,
in `data/calendar/festivals.yaml`. Each one has a theme, what its reel should say, and a
greeting for the morning story.

**Dates.** Hindu festivals follow the lunar calendar and move every year. The dates in the file
are estimates and are not checked. On the Festivals page, check the dates of the next few weeks
on drikpanchang.com (or your panchang), change any that differ, and press **Confirm**. The
dashboard reminds you of dates still to check for the next six weeks. Fixed national days
(Republic Day, Yoga Day, Independence Day, Gandhi Jayanti) are confirmed already.

**What a confirmed festival does.** That day's first reel takes the festival's theme and
topic, and the first story is its greeting. Both still come to you for approval.

**Other actions:**
- **Make post now:** the festival reel (gold frame look) and the greeting story (sunrise
  look) are made straight away and wait in the inbox.
- **Skip:** no festival post that day.
- **Add a festival or day:** a local festival, a temple's day, or anything else missing.

Your changes are saved in `state/festivals.json`. The data folder is read-only in Docker, and
the file itself stays unchanged.
