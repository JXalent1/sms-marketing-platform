# Auctions4America — Text Marketing Guide

How to use the system, what it costs, and what to do when something looks wrong.

This covers the screens that are live today. It gets updated as the category and
history screens land.

---

## Signing in

Go to your site address and sign in with the username and password we set up
together. The session lasts a week on the same browser, so you will not be asked
every day. If you ever want to force everyone out, tell us and we rotate the key.

Nothing in the system is reachable without signing in. There is no public page
other than the sign-in screen itself.

---

## The six screens

**Dashboard** — the short version of everything: how many contacts you have, what
went out recently, and how much of this month's allowance you have used.

**Contacts** — your buyer list. Upload a CSV and the system matches the columns to
the fields it needs, cleans up the phone numbers, and throws away duplicates.
Someone who appears in five different files still enters your list once.

**Campaigns** — where you write and send. Type the message, pick who gets it,
review, send.

**Opt-outs** — everyone who has told you to stop. You cannot text these people
again, and that is deliberate.

**Usage** — segments used this cycle against the 10,000 included, and what you owe
if you have gone past it.

**Settings** — your brand details, your sending number, your account.

---

## Sending a campaign

1. Open **Campaigns** and write your message.
2. Choose the list you are sending to.
3. Read the counter under the message box before you send. It tells you how many
   **segments** the message costs. This matters — see below.
4. Send. The system works through the list steadily rather than all at once,
   which is what keeps your number in good standing with the phone networks.

You can watch it go out in real time. If something is badly wrong — too many
failures in a row — the campaign stops itself rather than burning through your
whole list.

### Before it sends, it checks it can finish

The system will refuse to start a campaign it cannot afford to complete. This is
on purpose. The alternative is what happens on other platforms: a send stops
halfway, four thousand people get nothing, and you have no clean way to work out
who did and did not receive it. Better to be told "not yet" up front.

---

## Segments, and why emoji are expensive

Texts are billed in **segments**, not messages. A plain-text message is one
segment up to 160 characters. Go over that and it becomes two segments, then
three, and so on.

**Adding a single emoji changes the maths.** One emoji anywhere in the message
drops the limit from 160 characters to 70 — so a message that was one segment
becomes three, and costs three times as much to send to the same people. The
character counter warns you when this happens. It is not a bug; it is how the
phone networks encode text, and it applies everywhere.

If you want the emoji, use it. Just know that on a 5,000-person send, one emoji
is the difference between 5,000 segments and 15,000.

### Practical version

- Keep it under 160 characters and you pay for one segment per person.
- Write "and" instead of "&" — no difference in cost, but it reads better.
- An emoji roughly triples the cost of the send. Decide if it earns it.

---

## What it costs

- **No monthly fee.**
- **10,000 segments included every month.**
- **$0.015 per segment** after that.

The **Usage** screen shows where you are against the 10,000 for the current cycle
and what, if anything, you owe. Those are the live numbers — this document just
repeats the agreement.

For scale: a one-segment message to 8,000 people uses 8,000 segments and stays
inside the included allowance. The same message with an emoji uses 24,000, and
the 14,000 past the allowance would come to $210.

---

## Opt-outs

When someone replies STOP, they are removed immediately and automatically. The
same goes for UNSUBSCRIBE, CANCEL, QUIT, END, and a handful of other phrasings —
people do not always type the exact word, and the system reads intent rather than
demanding an exact match.

Three things worth knowing:

1. **It is instant.** No overnight sync, no window where they might get one more.
2. **It cannot be undone from your side.** If someone opts out by mistake, they
   have to text START themselves. This is a legal requirement, not a limitation
   we chose, and it protects you.
3. **Uploading a new CSV does not bring them back.** If someone who opted out
   appears in a file you upload later, they stay opted out. This is the single
   most common way businesses get themselves into trouble, and the system will
   not let it happen.

Every outgoing message includes opt-out wording. That is also a legal
requirement.

---

## When something looks wrong

**A message failed to send.** Most failures are the number itself — disconnected,
or a landline that cannot receive texts. The campaign view groups failures by
reason so you can see at a glance whether it is a handful of bad numbers or
something broader.

**Numbers you scraped or bought mostly fail.** Business numbers from directories
are usually landlines. The system filters those out before sending rather than
paying to text a fax machine, which is why an imported list often shows fewer
sendable contacts than rows in the file.

**Delivered vs sent.** "Sent" means it left the system. "Delivered" means the
handset confirmed it. Some carriers never send confirmation, so a message can be
genuinely delivered and still show as sent. You are only billed once per segment
either way.

**Nothing is going out at all.** Check the Dashboard first — if the system is in
test mode, campaigns are logged rather than sent. That is the mode we use while
setting up. Call us.

---

## Your data

Your contact list is backed up every night, and the backup is checked by
restoring it — not just written and assumed good. If the server were lost
entirely, your list is recoverable.

We do not share your list, and it is not pooled with anyone else's.

---

## Getting help

Call or text us. If it is about a specific campaign, mention the campaign name
and roughly when you sent it — that is enough for us to find it.
