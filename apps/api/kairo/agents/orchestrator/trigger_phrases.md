# PM Agent — Trigger Phrase Reference

Each section lists the intent, what triggers it, and example sentences you can say.

---

## Schedule — View (`LIST_STATE › target: schedule`)

**Triggers:** any of `show / list / what are / what's on / what is / what's in / what do i have / tell me my / read my` + `schedule / calendar`

```
What is in my schedule tomorrow?
What's in my calendar this week?
Show my schedule
Show me today's calendar
List my schedule for Friday
What do I have on my calendar?
What's on my schedule today?
What are my calendar events?
Tell me my schedule for tomorrow
Read my schedule
```

---

## Todos — View (`LIST_STATE › target: todos`)

**Triggers:** any of `show / list / what are / what is / what do i have` + `todo / task`

```
Show my todos
List my tasks
What are my tasks?
What is on my todo list?
What do I have as tasks?
```

---

## Habits — View (`LIST_STATE › target: habits`)

**Triggers:** any of the show/list words above + `habit`; OR just `habit / streak / check in / checkin`

```
Show my habits
List my habits
What are my habits?
Habit check
Check in
My streak
```

---

## Journal — View (`LIST_STATE › target: journal`)

**Triggers:** any of the show/list words above + `journal`; OR `journal / reflection / daily log` anywhere in message

```
Show my journal
List my journal entries
What's in my journal?
Journal
Daily log
```

---

## Schedule — Add event (`CREATE_SCHEDULE_EVENT`)

**Triggers (multiple paths — any of these works):**

**1. Explicit command + date + time:**
```
Schedule a meeting with Alex tomorrow at 3pm
Add team standup every Monday at 9am
Book dentist appointment on Friday at 2pm
Put gym on Tuesday at 7am
Create a deep work block today 2–4pm
Block off Thursday 10–11am for focus
```

**2. Life-event phrasing + time:**
```
I need to go to the gym tomorrow at 7am
I have a dentist appointment Friday at 2pm
I want to go for a run Monday at 6am
I'm going to lunch with Sarah on Thursday at noon
I wanna have breakfast on Saturday at 9am
I'm gonna do yoga every morning at 7am
```

**3. Recurring weekday + time:**
```
Every Tuesday and Thursday at 2pm deep work
Mondays at 9:30 team standup
Weekdays at 8am morning standup
```

**4. Date + time alone (bare slot):**
```
Tomorrow at 3pm client call
Friday 10am dentist
Next Monday 9–10am
```

---

## Schedule — Remove event (`REMOVE_SCHEDULE_EVENT`)

**Triggers:** leading removal verb (`delete / remove / cancel / clear / wipe`) + schedule noun; OR `delete / remove / cancel` + event keyword

```
Delete my 3pm meeting
Cancel the dentist appointment
Remove the standup on Friday
Clear my schedule for tomorrow
Cancel all my meetings today
Delete the team standup
Cancel that appointment with Alex
Remove everything on Thursday
```

---

## Schedule — Update / Reschedule (`UPDATE_SCHEDULE_EVENT`)

**Triggers:** `move / reschedule / update / change / push / shift` + event/calendar keyword or date/time

```
Move my 3pm meeting to 4pm
Reschedule the dentist to next Friday
Push the standup to 10am
Shift my afternoon meeting by 30 minutes
Change the team call from Monday to Wednesday
Update my lunch to 1pm
Reschedule tomorrow's meeting to Thursday at 2pm
```

---

## Schedule — Skip one occurrence (`SKIP_OCCURRENCE`)

**Triggers:** `skip` (without task/todo context)

```
Skip the standup this Friday
Skip my morning run tomorrow
Skip this week's team meeting
```

---

## Schedule — Move just one occurrence (`MODIFY_OCCURRENCE`)

**Triggers:** `just this / only this / this one / this occurrence / this instance` + `move / reschedule / change / update / shift`

```
Move just this one standup to 10am
Reschedule only this occurrence to Monday
Change just this meeting to 3pm
Update this instance to Friday
```

---

## Schedule — Cancel all future from date (`CANCEL_SERIES_FROM`)

**Triggers:** `all future / from now on / from today on / going forward` + `cancel / delete / remove / stop / end`

```
Cancel all future standups from next Monday
Delete all future gym sessions going forward
Remove all future meetings from today on
Stop all future yoga classes going forward
End the daily check-ins from tomorrow on
```

---

## Todos — Add (`CREATE_TODO`)

**Triggers:** starts with `add task / add a task / create task / create a task / new task / remind me`; or `todo` + `add / create / new`; or bare `add/create <something>` with no schedule/habit context

```
Add task buy groceries
Add a task: call the bank
Create task finish the report
New task review proposal
Remind me to call mom
Add a todo: renew passport
Create a new task for the quarterly review
Add pick up dry cleaning
Add buy milk
```

---

## Todos — Complete (`COMPLETE_TODO`)

**Triggers:** `complete / done / finish / finished / mark` + `task / todo`

```
Complete the "buy groceries" task
Mark the report task as done
Finish task call the bank
Done with the review todo
Mark "renew passport" finished
```

---

## Todos — Remove (`REMOVE_TODO`)

**Triggers:** `delete / remove / cancel` + `task / todo`

```
Delete the "buy groceries" task
Remove the report todo
Cancel that task about calling the bank
Delete my oldest todo
```

---

## Habits — Add / Check-in / Streak (`HABIT_ACTION`)

**Triggers:** `habit / streak / check in / checkin` anywhere in message

```
Add a habit: meditate 10 minutes daily
Create habit morning run
Check in on my meditation habit
Habit checkin
What's my streak for running?
Show my habit streak
Add a new habit
Log habit check-in
```

---

## Journal (`JOURNAL_ACTION`)

**Triggers:** starts with `log `; or `journal / reflection / daily log` anywhere

```
Journal today was productive and I finished the proposal
Log I worked out this morning
Daily reflection: feeling focused today
Write in my journal
Add a journal entry
Reflection: grateful for the long walk today
Log my thoughts on the sprint
Daily log
```

---

## Memory — Save fact (`SAVE_MEMORY`)

**Triggers:** starts with `remember / remember that`; or `export` + `private / memory`

```
Remember that I prefer meetings before noon
Remember I don't like back-to-back calls
Remember that my gym is called FitLife
Remember that I'm allergic to early morning slots
Export my private memory
```

---

## Coaching — Planning (`GENERAL_COACHING`)

**Triggers:** coaching keywords without stateful action verbs

```
Coach me through my morning
Help me plan my day (do not change anything)
Morning launch
Focus sprint
Calendar triage
Tiny win
Short day review
Daily check-in
Risk scan — what are the likely blockers today?
What are my top priorities?
Give me 3 wins for today
One useful action under 10 minutes
```

---

## Coaching — Habit suggestion (`GENERAL_COACHING`)

**Triggers:** `habit / routine` + suggestion keywords (no create/add/check-in verbs)

```
Suggest a habit for me
Recommend a routine
Coach me on building a good habit
Help me choose one tiny habit to start
What habit should I try this week?
Give me one useful routine suggestion
What routine could help me focus?
```

---

## Web Search (`GENERAL_COACHING › web_search`)

**Triggers:** web search keywords

```
Web search for best morning routines
Search the web for time-blocking techniques
Search online for productivity tips
Look up pomodoro technique
Search for healthy lunch ideas
Find coffee shops near me on Google
Google for standing desk benefits
Search the internet for deep work strategies
```

---

## Approval / Rejection (`APPROVE_ACTION` / `REJECT_ACTION`)

**Triggers:** starts with `approve / confirm / yes` → approve; starts with `reject / deny / no / cancel approval` → reject

```
Yes
Approve
Confirm
Yes, do it
Yes, go ahead

No
Reject
Deny
Cancel approval
```

> **Note:** If you say "yes and move it to 3pm" or "no, actually tomorrow", the agent treats it as ambiguous and waits for more context, since it combines an ack with a correction.

---

## Self-disclosure (auto-saved to memory)

These are not explicit commands — the agent passively records them when you describe yourself.

```
My favorite time to work is early morning
I prefer back-to-back meetings on Tuesdays
I love hiking on weekends
I usually wake up at 6am
I always have coffee before my first meeting
My routine is: gym, shower, work
I go for a run every Monday and Wednesday
I'm a morning person
I tend to procrastinate after lunch
I'm into reading before bed
```
