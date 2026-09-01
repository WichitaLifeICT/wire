# The Wire — Wichita news triage

Replaces scrolling Google local news and pasting links into Notion by hand.
Pulls every Wichita newsroom's feed, drops anything old, kills duplicates,
and gives you the six buckets with a keyboard.

## Run it

    python3 wichita_wire.py --discover     # first run only
    python3 wichita_wire.py                # every run after

Writes `wichita-wire.html` next to the script. Open it.
Standard library only. Keep `wichita_wire.py` and `wire_template.html` together.

## Options

    --days 3          how far back (default: 3)
    --keep-undated    keep stories with no publish date — off by default,
                      because undated entries are where ancient links hide
    --discover        re-sniff each outlet's homepage for its real feed URL
    --json FILE       also dump the stories as JSON
    --out PATH        write the page somewhere else

## The pass

1. `j` / `k` down the list. Every story shows its age; anything over three
   days old gets a red timestamp.
2. `1`–`6` files it: main story, local business, startup, nonprofit, civic,
   local. A dashed yellow outline marks the bucket the headline looks like —
   a guess, never automatic.
3. `b` drops into the blurb box for that story. Write it while the headline
   is in front of you.
4. **Copy for Notion** gives you the whole issue grouped under `##` headings,
   each story as bold title, link, blurb.

`0` unfiles. `/` searches. `o` opens. `Hide filed` clears what you've handled
so the list shrinks as you work.

Your filing and blurbs survive a reload — they're kept in the browser, per
machine. **Clear the board** wipes them.

## Editing the buckets

Top of `wire_template.html`, the `BUCKETS` array. Order sets both the keyboard
number and the export order. The `match` words only drive the suggestion
outline; `PRECEDENCE` below it decides which bucket wins when a headline hits
more than one list.

## When an outlet says "offline"

Run `--discover`. It reads each site's HTML and finds the feed the site itself
advertises, then caches it in `wire_feeds.json`. If that still fails, add the
URL by hand to that outlet's `feeds` list at the top of `wichita_wire.py` —
same place you add new sources.

## Note

Headlines and links are for deciding what to cover. Write your own blurbs.

---

## Deploying

The workflow in `.github/workflows/wire.yml` runs the script three times a day,
commits `dist/index.html`, and Netlify deploys on the commit.

Netlify settings:

- Build command: `echo skip`
- Publish directory: `dist`

Run it by hand any time from the repo's **Actions** tab → Build the wire →
Run workflow.

Cron is set for UTC-5 (Central, summer). Add an hour after the November change.
