"""Generate a toy ticket-routing dataset: route a support ticket to a queue code.

The codes are arbitrary (BILL-7, SHIP-2, ...), so a base model can't guess them and fine-tuning has
something real to learn. Deterministic: the same seed gives the same file.

    python make_data.py > tickets.jsonl
"""
import json
import random

QUEUES = {
    "BILL-7": ["I was charged twice for my {thing}", "my invoice for the {thing} looks wrong",
               "why did my card get billed for {thing}", "the price on my {thing} bill is too high"],
    "SHIP-2": ["my {thing} hasn't arrived yet", "tracking for my {thing} stopped updating",
               "the courier left my {thing} at the wrong address", "when will my {thing} be delivered"],
    "AUTH-4": ["I can't log in to manage my {thing}", "password reset email for my {thing} account never came",
               "two-factor code doesn't work when I open my {thing} settings", "my account is locked after ordering a {thing}"],
    "REF-9": ["I want my money back for the {thing}", "please refund the {thing} I returned",
              "how long does a refund for a {thing} take", "the {thing} was broken, I need a refund"],
    "GEN-1": ["do you sell a bigger {thing}", "what colours does the {thing} come in",
              "is the {thing} good for beginners", "can I get a gift wrap for the {thing}"],
}
THINGS = ["blender", "laptop stand", "desk lamp", "yoga mat", "kettle", "backpack", "headphones", "coffee grinder",
          "monitor", "office chair", "water bottle", "phone case", "air fryer", "keyboard", "tent"]
OPENERS = ["", "Hi, ", "Hello team, ", "Urgent: ", "Hey, ", "Good morning. "]
CLOSERS = ["", " Thanks.", " Please help.", " Order #{n}.", " This is the second time I'm asking."]
PROMPT = "Route this support ticket to a queue. Reply with the queue code only.\n\nTicket: {ticket}"

rng = random.Random(7)
for i in range(900):
    code = rng.choice(list(QUEUES))
    body = rng.choice(QUEUES[code]).format(thing=rng.choice(THINGS))
    ticket = (rng.choice(OPENERS) + body + rng.choice(CLOSERS).format(n=rng.randint(10000, 99999))).strip()
    ticket = ticket[0].upper() + ticket[1:]
    print(json.dumps({"id": f"t{i}", "prompt": PROMPT.format(ticket=ticket), "label": code, "queue": code}))
