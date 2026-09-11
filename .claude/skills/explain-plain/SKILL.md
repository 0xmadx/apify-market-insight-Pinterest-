---
name: explain-plain
description: Use whenever explaining ANYTHING to the operator — what broke, what you changed, how a part works, what a number means, or when they say "explain", "I don't understand", "for noob", "what is this", "am lost", or paste something back and ask about it. Plain words first, one ASCII diagram, then the workflow as numbered steps. Not advisory.
---

# Explaining things here

The operator built this business and knows it well. They are **not** a native
English speaker and **not** a career engineer, and they have said so directly:
*"explain for noob"*, *"i dont know check what ai told you am lost"*, *"am
tiered thinking in ur shoes"*.

So an explanation that is technically perfect and hard to read is a **failed
explanation**. It is not their job to decode it.

## The three parts, in this order, every time

### 1. Plain words first

Say what happened in ordinary language before any term from the code.

    BAD   `mark_blocked()` had zero callers, so eviction never fired on
          an auth_expired verdict.

    GOOD  When Pinterest rejected an account, nothing removed that account
          from the list. So the next customer got handed the same dead
          account. The code that was supposed to remove it existed, but
          nothing ever called it.

Rules that make the difference:

- **One idea per sentence.** Short sentences survive translation; long ones do not.
- **Name the thing, then the code word — once, in brackets.** "the list of
  accounts that can serve customers (`valid_profiles`)". After that, use the
  plain name.
- **Say the consequence in money, customers or time.** Not "a race condition" —
  "about one customer in five got an error, for up to 15 minutes".
- **Never delete the caveat to make it simpler.** Simplify the WORDS, never the
  TRUTH. If something is uncertain, say "I am not sure" in plain words too.
- **No emoji.** Standing rule from the operator, and it applies here as well as
  in customer-facing text.

### 2. One ASCII diagram

A picture of the FLOW, not of the code. Draw the thing moving through the
system. Use `before / after` when something was fixed — the contrast is what
makes it click.

Keep it under about 20 lines and inside a fenced code block so the terminal
does not reflow it. Plain `-` `|` `+` `>` travel everywhere; box-drawing
characters are fine here too, but never mix a diagram so wide it wraps.

    BEFORE — the dead account stayed in the pool

      customer --> actor --> account #3 --> Pinterest says NO
                                                  |
                                    actor fails. tells nobody.
                                    account #3 still in the pool
                                                  |
                            next customer --> account #3 again

Label the arrows with what is actually moving ("cookies", "a strike", "the
answer"), not with function names.

### 3. The workflow, as numbered steps

What happens, in order, with **what the operator would see**. Each step is one
line where possible.

    1. Pinterest refuses the account.
    2. The actor quietly switches to another account and retries.
       -> The customer sees nothing. They just get their data.
    3. The bad account gets one strike, written into Redis.
    4. Two strikes and it stops serving customers.
    5. You log in by hand in AdsPower.
    6. Within 5 minutes it is back in service. You run no command.

If a step is THEIRS to do, say so on that step. Never bury a human action in a
paragraph.

## Also true when you are reporting your own work

Same three parts. And two extra rules the operator has earned:

- **Lead with what it means for them**, not with what you edited. "Your four
  actors still run last week's code" comes before any filename.
- **Say plainly when you were wrong.** They cannot check your work line by
  line, so an unflagged mistake becomes permanent. Write the correction in the
  same plain words, not buried in a commit message.

## What this is not

Not baby talk, and not fewer facts. The operator catches real problems — they
found the dead-session loop, the Upstash bill, and the fingerprint gap. Give
them the whole picture in words they can act on quickly.

If an explanation would be long, it is usually because the THING is tangled.
Say that too, then draw it.
