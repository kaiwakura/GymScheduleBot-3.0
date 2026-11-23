# GymScheduleBot 3.0

**A muscle-building planner that reads your schedule and fits a well-designed training week around it.**

Most people don't skip the gym because they're lazy. They skip because their week got busy and their plan didn't change with it. GymScheduleBot looks at when you're actually free and builds a hypertrophy program around it. It doesn't fill every free slot: more workouts aren't automatically better, so it picks the split that trains each muscle well and leaves room to recover.

| Workouts that fit | Recommended split | Each major muscle trained |
|---|---|---|
| 1 | Full Body | 1× |
| 2 | Full Body A + B | 2× |
| 3 | Full Body A + B + C | 3× |
| 4 | Upper / Lower / Upper / Lower | 2× |
| 5 (or 6–7 free days) | Push / Pull / Legs + Upper / Lower | 2×, with two rest days |

Your **commitment** sets the most workouts per week: Very Light 1, Light 2, Moderate 3, High 4, Dedicated 5. Free time never adds more. Six-day Push/Pull/Legs happens only if you ask the AI Coach for it.

**Live demo:** coming soon (free hosting on Render, so the first load can take up to a minute). <!-- Replace with the Render URL after deploying. -->

**Version 3.0** is a separate, rebuilt version of [GymScheduleBot 2.0](https://github.com/kaiwakura/GymSchedule-Bot-2.0). It adds a calendar-first interface (any week, workout details, other available times, drag to move), an AI Coach that proposes changes before applying them, consistent hypertrophy defaults you can adjust by chat, and Google Calendar sync across weeks.

![The busy student sample week: workouts placed in the gaps between classes and shifts](docs/screenshot-busy-week.png)

![A workout's details: exercises, sets and reps, and other times that fit](docs/screenshot-workout-details.png)

## Features

**Your week, on a calendar**

- **Week view with the coach beside it.** Your commitments and workouts sit on one calendar; the AI Coach is in a panel next to it, so you can ask for a change and see it land.
- **Any week.** Step back and forward with the arrows, or jump with **Today**. Future weeks are planned on demand (recovery carries over from the weeks before), and past weeks show what you did, from your browser's recent history.
- **Workout details in a small popup.** Click a workout for its muscles, exercises with sets × reps (expand one for rest time and the muscles it trains), the estimated length and the effort cue.
- **Other available times.** Each workout lists up to three other times that are free (travel buffer included) and keep 48 hours from workouts that train the same muscles, this week and the weeks around it. Pick one, or drag the workout yourself; an **Undo** bar appears either way.
- **Moves are kept.** A workout you moved stays where you put it, and the rest of the week is planned around it. If a later change makes that impossible (say, you switched splits), the plan says what it dropped and why instead of moving it silently.
- **Edit availability.** Switch to **Edit availability** and drag across the calendar to mark time you can't train. These blocks only affect planning.

**Bringing in your schedule**

1. **Connect Google Calendar** to plan around your real week. The app reads only when you're busy, never what your events are, and you choose which calendars count.
2. **Send a screenshot** of a class timetable or calendar to the AI Coach. Gemini reads it and adds each class or shift as a weekly event.
3. **Add your own commitments**, like in Google Calendar: click any empty time, then **drag** to move, drag the bottom edge to resize, or click to edit. They're saved in your browser, so no account is needed.

Two realistic **sample weeks** (a busy student and a 9-to-5) let anyone try it without signing in. While a sample is loaded your own commitments are set aside, and Google isn't used or changed.

**The AI Coach suggests; you decide**

- Facts about your week ("I have soccer Tuesdays 6–7:30", "I skipped Sunday's workout") are applied right away.
- Changes that would move workouts ("move Thursday's workout to Friday morning", "I prefer mornings") are shown first as a **proposal**: dashed outlines where workouts would go, a "Moving" flag on the ones that would leave, and Accept / Reject.
- Exercise changes apply right away: "simpler exercises", "fewer sets", "heavier weights, fewer reps", "no barbell rows", "back to the defaults".
- Gemini turns each message into structured changes; without an API key, a rule-based parser handles the same phrases.

**Preferences**

One panel for everything: which calendars count as busy, your own commitments, commitment level, workout length, preferred time, travel buffer and split (for this week only, or until you change it). Changes are a draft until you press **Apply changes**, which re-plans once; **Cancel**, the close button or clicking outside discard the draft. If applying fails, your previous settings stay. Applying never writes to Google Calendar.

**Training**

- **Hypertrophy programming with consistent defaults:** every plan starts from the same evidence-informed defaults (about 3 working sets per exercise, moderate rep ranges, 1–3 reps short of failure). There's no experience setting; instead, ask the coach for easier alternatives, different movements, more or fewer sets, or heavier or lighter rep ranges.
- **Every muscle counts, calves and core included:** chest, back, shoulders, biceps, triceps, quads, hamstrings, glutes, calves and core are all programmed and tracked. If a workout is too short for all of them, the plan says which ones were left out.
- **Realistic workout length:** 45, 60, 75 or 90 minutes. The estimate counts warm-up, setup, sets and rest (2–3 minutes after compound lifts, 1–2 after isolation work).
- **Weekly volume you can trust:** sets per muscle count 1 for the muscles an exercise targets and 0.5 for the ones that help. The plan says so when a week can't reach about 10 sets per muscle.
- **Recovery measured in hours, across weeks:** sessions sharing a muscle start at least 48 hours apart, and the planner prefers 72. Last week's workouts count too, so Sunday's leg day still matters on Monday. Tell the coach you skipped one and it stops counting.
- **Travel buffer:** time kept free between a workout and anything busy (None to 60 minutes), for getting to and from the gym. It only affects when workouts can go; your events are never changed.
- **Explains itself:** "How this plan was decided" says why it chose that split and shows weekly sets per muscle.

**Google Calendar sync and export**

- Workouts go into their own **GymScheduleBot** calendar. Before anything changes you see a preview, grouped by week, and nothing happens until you confirm. Edits you make in Google win (see [below](#google-calendar-sync)).
- Download any week as an `.ics` file for other calendar apps.

## How it works

```
 calendar source ──► busy blocks ──► free slots ──► choose split ──► build workouts ──► place sessions ──► weekly plan
 (Google / your events /             (availability)  (splits)        (sessions)         (planner)              │
  screenshot / sample week)                                                                                    ▼
 AI Coach message ──► Gemini / rule parser ──► facts applied now; moves shown    coach reply, .ics,
                      (ai/)                      as a proposal (proposals.py)        Google Calendar
 drag / other times ──► moved workouts (workout_moves.py) ──► kept where you put them by the planner
```

`planning.py` ties it together for any week: it gathers the busy time that applies (Google, your commitments, time you can't train), the moved and already-done workouts, and the weeks before it for recovery, then calls the planner.

**Design decision: AI translates, code decides.** The language model only turns chat messages into structured setting changes (validated against a JSON schema) and writes a short reply. Splits, exercises, sets, times and recovery are all decided by deterministic, unit-tested Python, and the facts in each coach reply come from that code. Plans are predictable and testable, API costs stay tiny, and the whole app works with no API key.

### The scheduling algorithm

1. **Free slots** (`scheduler/availability.py`): pad every busy block with the travel buffer, clip to the allowed hours, merge overlaps, and keep the gaps long enough for a workout.
2. **Split choice** (`scheduler/splits.py`): a split you asked for is tried first. Otherwise the planner aims for `min(commitment cap, free days)` workouts and uses the table above. Splits that train every major muscle twice are always tried before ones that train them once.
3. **Workouts** (`scheduler/sessions.py`): each workout template is a list of slots ("a quad exercise", "a calf exercise"), each with a few exercises that can fill it. The builder takes the first exercise that suits your coach requests (simpler exercises, ones to avoid), gives every required exercise 2 working sets, then adds sets up to the goal (3 by default) until the next set wouldn't fit the time. Very short workouts drop to single sets before anything is cut.
4. **Placement** (`scheduler/planner.py`): workouts you moved (or already started) are fixed first. Then it tries every way of placing the rest on the free days, with up to 3 start times per day. That's at most C(7,k) × 3^k options, a few thousand, so it checks them all. Any placement where two sessions sharing a muscle (including recent ones from before the plan) start under 48 hours apart is rejected. The rest are scored on rest short of 72 hours, uneven spacing, and distance from your preferred time.
5. **Fallbacks:** if a split doesn't fit, it tries the next option. For example, with only Monday and Tuesday free, two full-body workouts would train the same muscles on back-to-back days, so you get Upper on Monday and Lower on Tuesday, and the plan explains why.

## Training approach

GymScheduleBot separates what research supports from programming choices the app makes on purpose.

**What the research supports**

- **Weekly volume matters most for growth.** Muscle growth rises with weekly sets, with diminishing returns ([Pelland et al., 2026](#scientific-references)). The ACSM's 2026 position stand found hypertrophy was enhanced by about 10 or more sets per muscle per week ([Currier et al., 2026](#scientific-references)). Experienced lifters may benefit from more, around 12–20 sets ([Baz-Valle et al., 2022](#scientific-references)).
- **Counting compound sets fractionally.** Counting sets for helper muscles as half a set described the research data best ([Pelland et al., 2026](#scientific-references)), so that's how the weekly counter works.
- **Frequency and split type matter less than volume.** With weekly volume equal, training a muscle more often didn't independently increase growth ([Pelland et al., 2026](#scientific-references); [Currier et al., 2026](#scientific-references)), and split and full-body routines produced similar results ([Ramos-Campo et al., 2024](#scientific-references)).
- **Train close to failure, but you don't need to reach it.** Training to failure wasn't clearly better than stopping short ([Refalo et al., 2023](#scientific-references)), while stopping closer to failure was associated with more growth ([Robinson et al., 2024](#scientific-references)). The app prescribes about 1–3 reps in reserve.
- **Rep ranges are flexible.** With volume matched, heavier and lighter loads built similar muscle ([Carvalho et al., 2022](#scientific-references)), so 6–12 reps on compound lifts and 10–20 on isolation work are practical choices, not requirements.
- **Rest a bit longer between sets.** Rest periods over 60 seconds showed a small benefit for growth ([Singer et al., 2024](#scientific-references)); the app uses 2–3 minutes after compound lifts and 1–2 after isolation work.

**Programming choices (GymScheduleBot's defaults, not research findings)**

- **Twice a week per muscle when the schedule allows.** Research doesn't show that twice is better than once at equal volume. The reason is practical: spreading a week's sets over two sessions keeps each workout a manageable size. That's why two free days get Full Body A + B rather than Upper + Lower.
- **48 hours minimum between sessions for the same muscle, 72 preferred.** A conservative rule of thumb; recovery varies with effort, volume, experience and the individual, so every plan leans clearly toward 72 hours.
- **Five workouts at most by default, with two rest days**, even with seven free days. Six-day Push/Pull/Legs isn't worse, it's just not the default; ask the coach for it.
- **One set of defaults for everyone, adjustable through the coach.** About 3 working sets per exercise, which puts most muscles at 10 or more weekly sets with two sessions each, and up to around 20. The app doesn't ask for or guess your experience level; easier alternatives, different movements and more or less volume are a message away. These are starting points to adjust, not thresholds.
- **Calves and core are programmed like every other muscle:** direct work about twice a week, loaded exercises, and counted in weekly volume.
- **Rest-time estimates** (2.5 minutes after compound sets, 1.5 after isolation, 1.5 minutes of setup per exercise, an 8-minute warm-up) are planning assumptions for timing workouts.

## Scientific References

1. Currier BS, D'Souza AC, Fiatarone Singh MA, et al. **American College of Sports Medicine Position Stand. Resistance Training Prescription for Muscle Function, Hypertrophy, and Physical Performance in Healthy Adults: An Overview of Reviews.** *Medicine & Science in Sports & Exercise*, 58(4):851–872, 2026. [doi:10.1249/MSS.0000000000003897](https://doi.org/10.1249/MSS.0000000000003897)
2. Pelland JC, Remmert JF, Robinson ZP, et al. **The Resistance Training Dose Response: Meta-Regressions Exploring the Effects of Weekly Volume and Frequency on Muscle Hypertrophy and Strength Gains.** *Sports Medicine*, 56:481–505, 2026. [doi:10.1007/s40279-025-02344-w](https://doi.org/10.1007/s40279-025-02344-w)
3. Ramos-Campo DJ, Benito-Peinado PJ, Caravaca LA, Rojo-Tirado MA, Rubio-Arias JÁ. **Efficacy of Split Versus Full-Body Resistance Training on Strength and Muscle Growth: A Systematic Review With Meta-analysis.** *Journal of Strength and Conditioning Research*, 38(7):1330–1340, 2024. [doi:10.1519/JSC.0000000000004774](https://doi.org/10.1519/JSC.0000000000004774)
4. Robinson ZP, Pelland JC, Remmert JF, Refalo MC, Jukic I, Steele J, Zourdos MC. **Exploring the Dose–Response Relationship Between Estimated Resistance Training Proximity to Failure, Strength Gain, and Muscle Hypertrophy: A Series of Meta-Regressions.** *Sports Medicine*, 54(9), 2024. [doi:10.1007/s40279-024-02069-2](https://doi.org/10.1007/s40279-024-02069-2)
5. Singer A, Wolf M, Generoso L, et al. **Give it a rest: a systematic review with Bayesian meta-analysis on the effect of inter-set rest interval duration on muscle hypertrophy.** *Frontiers in Sports and Active Living*, 6:1429789, 2024. [doi:10.3389/fspor.2024.1429789](https://doi.org/10.3389/fspor.2024.1429789)
6. Refalo MC, Helms ER, Trexler ET, Hamilton DL, Fyfe JJ. **Influence of Resistance Training Proximity-to-Failure on Skeletal Muscle Hypertrophy: A Systematic Review with Meta-analysis.** *Sports Medicine*, 53(3):649–665, 2023. [doi:10.1007/s40279-022-01784-y](https://doi.org/10.1007/s40279-022-01784-y)
7. Baz-Valle E, Balsalobre-Fernández C, Alix-Fages C, Santos-Concejero J. **A Systematic Review of the Effects of Different Resistance Training Volumes on Muscle Hypertrophy.** *Journal of Human Kinetics*, 81:199–210, 2022. [doi:10.2478/hukin-2022-0017](https://doi.org/10.2478/hukin-2022-0017)
8. Carvalho L, et al. **Muscle hypertrophy and strength gains after resistance training with different volume-matched loads: a systematic review and meta-analysis.** *Applied Physiology, Nutrition, and Metabolism*, 2022, 357–368. [doi:10.1139/apnm-2021-0515](https://doi.org/10.1139/apnm-2021-0515)

## Google Calendar sync

**What the app can access.** It asks for three narrow permissions and nothing more:

| Scope | Used for |
|---|---|
| `calendar.events.freebusy` | when your calendars are busy (start and end times only, never titles, places or guests) |
| `calendar.calendarlist.readonly` | the names of your calendars, for the "which calendars count" checklist |
| `calendar.app.created` | creating the GymScheduleBot calendar and managing events on it, and no other calendar |

Because of the last scope, the app **can't** read, change or delete anything on your other calendars, even by mistake.

**The GymScheduleBot calendar.** It's created the first time you connect and found again when you reconnect, so it's never duplicated. It holds only workouts, so you can hide it, recolor it or turn off its notifications in Google Calendar like any other calendar. It never counts as "busy" (your workouts don't block themselves), but its workouts still count for recovery.

**Choosing calendars.** Every calendar you have turned on in Google counts as busy time by default. **Preferences → Calendars & commitments** lists them to leave some out (a shared family calendar, say); the change is applied together with your other preferences. The app stores a short hash of each calendar you leave out, not its name or id.

**Syncing.** Click the **Google Calendar** button to compare the plan with what's already in the GymScheduleBot calendar and see what would change, grouped by week:

- **Add:** a new workout. **Move:** a different day or time. **Update:** same time, different exercises or length. **Remove:** a workout the new plan no longer has.
- Workouts that already match are left alone, so syncing twice changes nothing.
- **Which weeks:** the week on screen (you can go to any future week and sync it), plus this week and any week up to 8 weeks ahead that you synced before or moved workouts in, when they have changes. Untick a week to leave it as it is. Weeks you never touched aren't generated, so changing a preference never fills months of your calendar, and applying preferences never syncs by itself.
- Workouts you moved in the app are synced where you put them.
- Each workout has a predictable event id based on its day, so a sync that's retried, or run after a refresh or reconnect, updates events instead of making duplicates.
- Past workouts are never touched.
- If the plan changes between the preview and **Confirm** (for example, an event was added in another tab), the app asks you to review the new preview instead of applying an old one.
- If some changes fail (Google is briefly unavailable), the ones that worked are kept and syncing again finishes the rest.

**Your edits in Google win.**

- **Moved a workout?** The app notices (each event remembers where it was planned), keeps it where you put it, and plans the rest of the week around it with the usual 48-hour recovery rule. It's never moved back.
- **Moved it somewhere recovery can't work** (two Upper workouts a day apart)? Nothing is undone. The plan shows a "Heads up" note explaining the conflict and what you can do: move one of them in Google, or delete one and sync again.
- **Deleted a workout?** It stays deleted. If the plan still wants a workout that day, the preview lists it as **Restore** with an unticked checkbox, so it only comes back if you ask.

**Staying up to date.** The app checks Google when you open or reload the page. In between, Google's answers are cached for 5 minutes. If Google can't be reached, the last answer is used and the page says so; you stay connected. You're only disconnected if Google says your access was revoked or expired.

**Disconnecting** revokes the app's access at Google and deletes the stored tokens. The GymScheduleBot calendar stays in your Google account until you delete it.

## Tech stack

Python 3.11+ · FastAPI · Jinja2 · Google Calendar API (OAuth 2.0 with PKCE) · Google Gemini API (structured JSON output) · icalendar · pytest · ruff · GitHub Actions

## Run it locally

```bash
git clone https://github.com/kaiwakura/GymScheduleBot-3.0.git
cd GymScheduleBot-3.0
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional: works with no keys at all
uvicorn app.main:app --reload
```

Open http://localhost:8000 and try the **Busy student week** sample.

Run the tests and linter:

```bash
pytest -q
ruff check . && ruff format --check .
```

### Optional: turn on AI

Add `GEMINI_API_KEY=...` to `.env` (get a free key at https://aistudio.google.com/apikey). The model is set by `GEMINI_MODEL`. Note: on Gemini's free tier, Google may use requests to improve its products, so the app only sends your chat messages, schedule screenshots you choose to upload, and a summary of the plan (split, workout days and times). It never sends your Google Calendar events.

### Optional: connect Google Calendar

You need your own Google Cloud OAuth client. Never paste the client secret anywhere except `.env` (local) or Render's environment settings.

**1. Google Cloud project**

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project (for example "GymScheduleBot").
2. **APIs & Services → Library**: search for **Google Calendar API** and click **Enable**.

**2. OAuth consent screen** (APIs & Services → OAuth consent screen, or **Google Auth Platform**)

1. User type **External**. Fill in the app name, a support email and a developer contact email.
2. **Data access / Scopes → Add or remove scopes**: add these three (paste each into "Manually add scopes"):
   ```
   https://www.googleapis.com/auth/calendar.events.freebusy
   https://www.googleapis.com/auth/calendar.calendarlist.readonly
   https://www.googleapis.com/auth/calendar.app.created
   ```
3. **Audience / Test users**: add the Google accounts that should be able to sign in (yours first).

**3. OAuth client**

1. **Credentials → Create credentials → OAuth client ID → Web application.**
2. **Authorized redirect URIs**, one per place the app runs:
   ```
   http://localhost:8000/auth/google/callback
   https://<your-service>.onrender.com/auth/google/callback
   ```
3. Copy the client ID and client secret. Google shows the secret once; you can create a new one later if you lose it.

**4. Local `.env`**

```bash
GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-client-secret>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
TOKEN_ENCRYPTION_KEY=<output of the command below>
SESSION_SECRET=<output of the second command below>
OAUTHLIB_INSECURE_TRANSPORT=1   # local http only
```

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_hex(32))"
```

Restart the server and click **Connect Google Calendar**.

**5. Render** (Dashboard → your service → **Environment**; see [Deploy to Render](#deploy-to-render))

| Key | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | your client ID |
| `GOOGLE_CLIENT_SECRET` | your client secret |
| `GOOGLE_REDIRECT_URI` | `https://<your-service>.onrender.com/auth/google/callback` |
| `SESSION_SECRET` | a new random value (second command above) |
| `TOKEN_ENCRYPTION_KEY` | a new Fernet key (first command above), different from your local one |
| `GEMINI_API_KEY` | your Gemini key (optional; without it the coach uses its keyword parser) |
| `AI_DAILY_LIMIT` | optional: Gemini calls per day for the whole site (default 300) |

Don't set `OAUTHLIB_INSECURE_TRANSPORT` on Render. Save, and Render redeploys.

Why `SESSION_SECRET` matters on Render: it signs the session cookie. Without it the app makes up a random one at startup, and Render's free tier restarts the server after 15 minutes idle, which would sign everyone out. For that reason the app keeps Google sign-in turned off in production until `SESSION_SECRET` is set. Changing either key later just signs everyone out; they reconnect.

**6. Try it**

1. Connect, and check that a **GymScheduleBot** calendar appears in Google Calendar (it may take a refresh).
2. If the busy student sample is loaded, click **Use my own schedule** (sample weeks never use Google).
3. **Preferences**: untick one calendar, **Apply changes**, and check that its events stop blocking time.
4. Click **Google Calendar**, review the preview, **Confirm sync**. Sync again: it should say everything is already in sync.
5. Go to next week, move a workout, and open the sync preview: both weeks are listed, and only the ticked ones are written.
6. In Google Calendar, drag a workout to another free time, then reload the page: the plan keeps it there and reworks the rest. Delete another one and sync: it isn't recreated, and appears as an unticked **Restore** option.
7. **Disconnect Google Calendar** (in Preferences), then reconnect: the same calendar is reused, and syncing creates no duplicates.

**Google's testing mode.** A new app starts in **Testing**:

- Only the test users you added can sign in (up to 100).
- Google signs testers out **7 days** after they approve access, so they reconnect weekly. The app treats that as a normal expired connection.
- The consent page warns "Google hasn't verified this app". Testers click **Continue**.

To let anyone sign in, set the publishing status to **In production** and submit the app for Google's verification. For these scopes that means a privacy policy and home page on a domain you own, a short video of how the app uses the permissions, and Google's review, which can take several weeks. Until then, the sample weeks let anyone try the planner without Google.

## Deploy to Render

1. **New → Web Service**, connect the `GymScheduleBot-3.0` repository, and choose the free instance type.
2. **Build command:** `pip install -r requirements.txt` · **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. **Environment:** add the variables from step 5 above. `SESSION_SECRET` and `TOKEN_ENCRYPTION_KEY` are required for Google sign-in; `GEMINI_API_KEY` turns on the AI coach. Generate new values for production instead of reusing local ones.
4. Once Render shows the service URL (`https://<your-service>.onrender.com`), set `GOOGLE_REDIRECT_URI` to that URL plus `/auth/google/callback`, and add the same address to the OAuth client's **Authorized redirect URIs** in Google Cloud.

Nothing in the code depends on the URL; it only appears in `GOOGLE_REDIRECT_URI` and in Google Cloud.

**After the first deploy, check:**

- The page loads over HTTPS, and Connect Google Calendar → sync works (step 6 above).
- **The AI limits identify visitors correctly.** The app trusts Cloudflare's `CF-Connecting-IP` only on Render (see [Security notes](#security-notes)). In the browser console on the live site, run:

  ```js
  for (let i = 0; i < 7; i++) {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json",
      "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
      "X-Forwarded-For": `1.2.3.${i}`, "True-Client-IP": `5.6.7.${i}` },
      body: JSON.stringify({ message: "hi", events: [] }) });
    console.log(i, r.status, (await r.json()).reply);
  }
  ```

  The 6th and 7th replies should mention the "simpler parser" (faked headers don't earn a new limit). Then send one request with a made-up `CF-Connecting-IP` header: Cloudflare should refuse it with a 403. If instead every request seems to share one limit, Render has stopped passing the header; the app then fails safe (stricter), and the header logic in `app/security.py` is the place to look.

## Project structure

```
app/
  main.py                 FastAPI routes: page, plan, preferences, AI Coach chat and proposals, Google sign-in and sync
  planning.py             planning any week: busy time, moved and done workouts, recovery from earlier weeks, other times
  proposals.py            coach changes that would move workouts, kept until you accept or reject them
  workout_moves.py        workouts you moved (kept in the browser), validated on every request
  google_auth.py          Google sign-in, token refresh and revoke, the encrypted connection
  google_integration.py   reading Google (with a short cache) and running a sync for one visitor
  calendar_sync.py        the sync itself: event ids, reading your edits, preview, applying changes
  security.py             CSRF protection for anything that changes data
  settings.py             a visitor's saved settings (session cookie), validated and migrated on every load
  training_history.py     recent workouts (kept in the browser) for recovery across weeks
  views.py                plan -> calendar columns, workout popups and week navigation
  models.py               shared data types
  ics_export.py           the plan as a downloadable .ics file
  scheduler/
    availability.py       busy blocks -> free slots
    exercises.py          exercise catalog: muscles trained, equipment, technique
    sessions.py           workout templates + building a timed workout from the coach's requests
    splits.py             split tables and the rules for choosing one
    volume.py             weekly sets per muscle (fractional counting) and targets
    planner.py            recovery-aware placement, fallbacks and explanations
  sources/
    google_calendar.py    Google Calendar API calls: calendars, free/busy, events, error handling
    custom.py             your own events: validation + expanding weekly/one-time events
    sample_weeks.py       the two sample weeks, loaded as editable events
  formatting.py           two tiny text helpers ("7:05 PM", "a, b and c") shared across modules
  ai/
    chat_changes.py       what a chat message asks to change, as plain data
    assistant.py          Gemini: reading messages and screenshots (structured JSON output), replies
    rule_parser.py        keyword parser used without an API key or when Gemini fails
  templates/              page, the re-renderable plan partial, the Preferences panel
  static/                 style.css, app.js (calendar, popups, dragging, chat, Preferences, sync; no build step)
tests/                    unit + integration tests (google_fake.py stands in for Google's API)
```

## Roadmap

- [ ] **Workout logging and progress tracking:** mark workouts done, log sets and weights, and suggest progressive overload
- [ ] **Equipment options:** dumbbells only, home gym (the exercise catalog already records equipment)
- [ ] **Store users in a database:** SQLite or Postgres instead of cookie sessions + browser storage, so your events follow you across devices
- [ ] **Weekly auto-planning:** an opt-in Sunday-night job that syncs next week's plan
- [ ] **"Travel time already included"** for commitments that already account for getting to the gym, so the travel buffer skips them
- [ ] **Server-side sessions** for Google tokens (the cookie code is already behind three functions)
- [ ] **Google verification** so anyone can connect, not just test users
- [x] **AI Coach chat** that changes and remembers preferences
- [x] **Schedule screenshots** read by Gemini
- [x] **Your own events** (weekly or one-time), like Google Calendar
- [x] **Hypertrophy programming** with consistent defaults the coach can adjust, calves and core, realistic timing and fractional volume
- [x] **Calendar-first design:** any week, workout popups, other available times, drag to move with undo, coach proposals, a Preferences panel with Apply
- [x] **Requested splits** through the coach, for one week or from now on
- [x] **Recovery across weeks**, with skipped workouts
- [x] **Google Calendar sync** with a preview grouped by week, its own calendar, and respect for edits made in Google

## Limitations

- **Your data lives in this browser.** Commitments, moved workouts and recent history are kept in localStorage, and settings in a signed cookie. There are no accounts, so nothing follows you to another device, and clearing site data starts you over.
- **Google sign-in is in testing mode.** Only test users can connect, and Google asks them to reconnect every 7 days (see above).
- **Past weeks** show only what this browser remembers (about 8 weeks). **Weeks far ahead** are planned from up to 3 weeks before them, so their recovery across week boundaries is an approximation until you get closer.
- **Without a Gemini key** the coach understands common phrasings only, and screenshots can't be read.
- **Every workout assumes a full gym**, and there's no workout logging or progress tracking yet (see the roadmap).
- **One server process:** the short Google cache lives in memory, which is fine for a single Render instance but would need a shared cache to scale out.
- **Not medical advice.** The defaults follow the research below, but they're general starting points, not an individual program.

## Security notes

- **Least access:** three narrow Google scopes (see [Google Calendar sync](#google-calendar-sync)). The app can only change the calendar it created, and it never sees event titles, descriptions or guests from your other calendars.
- **Tokens stay unreadable:** Google tokens are encrypted with Fernet (`TOKEN_ENCRYPTION_KEY`) and kept in the signed session cookie, which is HttpOnly (JavaScript can't read it), Secure (HTTPS only) and SameSite=Lax. Each visitor's cookie holds only their own tokens, so one person's sign-in can't reach another's calendar. The client secret and tokens never reach the page.
- **CSRF:** every request that changes something (preferences, chat, proposals, sync, disconnect) must carry a per-session token. Sign-in checks OAuth `state` and uses PKCE.
- **Gemini key stays on the server:** it's read from the environment when a request needs Gemini and sent to Google in a request header. It never appears in the page, JavaScript, API responses or logs (tests check all four).
- **AI quota protection:** the coach endpoints are public, so Gemini calls are capped at 5 a minute and 40 a day per visitor, and `AI_DAILY_LIMIT` (300 by default) for the whole site. Past a limit the coach keeps working with its keyword parser and says so. Requests over 6 MB are refused before they're read, and screenshots are capped at 4 MB. Chat messages can be up to 2,000 characters; longer ones get a clear warning instead of being cut.
- **Identifying visitors on Render:** Render serves every app through Cloudflare, which sets `CF-Connecting-IP` to the visitor's address and rejects requests that send their own. The app trusts that header only when running on Render (`RENDER=true`, set by Render) and only if it holds one valid IP; `X-Forwarded-For` and `True-Client-IP` are never trusted, because a client can write them. IPv6 visitors are grouped by /64.
- **No secrets in logs:** errors are logged by type and status code only, never tokens, Google's responses or what the user typed.
- **Disconnect** revokes access at Google, not just in the app.
- **Tradeoff:** a cookie is limited to about 4 KB and can't be revoked from the server. That's fine for a portfolio app (the encrypted connection is well under the limit, and a test checks it); a production version would keep tokens in a database and put only a session id in the cookie.
- Secrets come from environment variables, and `.env` files are git-ignored.
- **Tradeoff:** the AI limits are counted in memory, which suits a single Render instance; a restart resets them, and running several instances would need a shared store such as Redis.

## License

MIT
