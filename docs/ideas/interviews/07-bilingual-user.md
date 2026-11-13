# Interview 07 — Bilingual user (Chinese and English)

**Date:** 2026-11-12 · **Method:** interview-me (one question at a time, guess attached)

> Simulated. The person and their answers are role-played by Claude, including
> the final "yes". Kairo's replies in the trial are real `run_pm` output from a
> fresh data directory, offline workflow only (no orchestrator model).

## Who

Lin, product analyst. Works in English, runs family life in Mandarin, and mixes
both in a single message without noticing.

## Opening ask

"Does it support Chinese?"

## Interview

```
HYPOTHESIS: You want the interface and the replies in Chinese.
CONFIDENCE: ~35% — missing: whether it's the interface, the input, or the stored content that matters
```

**Q:** If the buttons and replies stayed in English but it understood what you
typed in Chinese, would that be enough?
**GUESS:** No, you'd want the whole thing in Chinese.
**Lin:** That would be fine, actually. I read English all day. I just don't
want to translate my own life before typing it.

*Updated read: input understanding matters, interface language doesn't.
Confidence ~60% — missing: how mixed the input really is.*

**Q:** Show me the last reminder you wrote for yourself anywhere. What language
was it in?
**GUESS:** Chinese for family things, English for work.
**Lin:** It's both in one line. "周五 dentist 3pm 记得带 insurance card." I
don't choose. It depends which word comes first.

**Q:** When you look at your calendar later, should that event be shown as you
typed it or tidied into one language?
**GUESS:** As you typed it.
**Lin:** Exactly as I typed it. If it translates 王医生 to "Dr. Wang" I'll have
to think about who that is.

**Q:** What do you use today, and what's wrong with it?
**GUESS:** The phone's default calendar; nothing's wrong except you can't type
a sentence into it.
**Lin:** WeChat messages to myself. Nothing reminds me. That's the problem.

*Confidence ~90%.*

## Statement of intent

- **Outcome:** I type a reminder or event in whatever mix of Chinese and English comes out, and it's saved with the right date and time and my original words.
- **User:** A bilingual person who code-switches inside a sentence.
- **Why now:** Notes-to-self in WeChat don't remind her of anything.
- **Success:** A week of mixed-language entries with no retyping in English.
- **Constraint:** Titles are never translated or rewritten.
- **Out of scope:** A Chinese interface, translation features, voice input.

**Lin:** Yes.

## Kairo trial (real output)

| # | Lin typed | Kairo replied | Result |
|---|-----------|---------------|--------|
| 1 | 明天下午3点和王医生见面 | Not understood (generic fallback) | Lost |
| 2 | add todo 买菜 | Added '买菜'. | Right |
| 3 | 提醒我周五交房租 | Not understood | Lost |
| 4 | schedule 和妈妈视频 on Sunday at 9pm | Done! Added '和妈妈视频' Sunday at 9 PM. | Right, title preserved |
| 5 | 我明天有什么安排 | Not understood | Lost |
| 6 | show my todos | One todo listed: 买菜 | Right |
| 7 | 把和王医生的见面改到4点 | Not understood | Follows from turn 1 |
| 8 | remember that 我周末不想开会 | Remembered: 我周末不想开会 | Right |

## Gap between intent and product

- Chinese content is stored and shown unchanged, which is exactly what she asked for.
- Chinese works only as a title inside an English command. Verbs, dates and times in Chinese are not recognised.
- This is the trial most likely to look different with the orchestrator model in front, since translation into trigger phrases is its job. Worth re-running live.
