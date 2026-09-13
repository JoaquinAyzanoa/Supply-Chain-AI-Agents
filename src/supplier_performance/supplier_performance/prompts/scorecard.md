Task: write the scorecard of one supplier for the purchasing team, from the numbers given. Write in {{language}}.

Return JSON with two keys:
- scorecard: one paragraph (60 to 120 words) a buyer reads in ten seconds: how reliable the supplier is on dates and quantities, how fast they answer, whether prices move, and what to watch. Use the numbers as given; never invent or round them into something they are not. When a metric has no data, say "no data yet" for it instead of guessing.
- trends: up to three short flags (under 12 words each) about changes since the previous run, only when the facts list them; an empty list otherwise.

No greetings, no headings, no advice about other suppliers.
