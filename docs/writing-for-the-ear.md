<!-- Instruction template for agents (CLAUDE.md, AGENTS.md or Cursor User Rules).
     Replace <repo-1>, <repo-2> with your work projects. -->

# How to work with me

Write your chat messages in my language (the language I write to you in), even though this template is in English.

I don't read the generated code or test manually: you implement and verify. Your chat messages are my only source for understanding what was done, and I listen to them out loud while you work.

## Verify it yourself
When you implement or fix something, test the behavior end to end before saying you're done. Testing here does not mean unit tests or code-level tests: it means actually using the app, walking through the flow that changed the way a user would, and checking the error and empty states when they make sense.
- Website or web app: in the browser.
- iOS app: in the iOS Simulator, driven by Maestro, which is already installed on this machine (`~/.maestro/bin/maestro`). Use a Device Hub simulator, preferably the iPhone 17 with the latest iOS version.
- Android: don't test it. App testing is iOS only and web testing is in the browser, unless I explicitly ask for Android in the task. Don't start an Android emulator or build for Android on your own.
- Before building, check whether you really need to. In React Native, if the native dependencies haven't changed and the app is already installed on the simulator, just reload the JavaScript, with no new build.

Builds and automated tests help, but they don't count as this test.

Always run it and see it working before you finish. A small change calls for a shorter test, but never no test.

Run the test in the background whenever the tool allows, with a subagent or a background command that notifies you when it finishes. Kick off the test and carry on with the next steps instead of sitting idle waiting. If the rest of the work finishes first, you can send the final message saying what has been done and that the test is still running. When the test comes back with a result, pick it up without me asking: if it found a problem, fix it and test again; once everything is verified, send a new final message with the result.

If the tool has no way to notify you after you end your reply, don't end it before the test finishes. Run it in the background while you do the rest, but wait for the result before the final message.

If the project has its own skill or instructions for testing and evidence, follow them. Only say something couldn't be tested after genuinely trying, telling me what you tried and what got in the way, such as a missing account, data, device or external service. This doesn't apply to code reviews or to questions.

## Chat messages
These rules apply only to what you write to me in the chat. Never to code, commits, PRs, documentation, tickets or other files. When a skill defines the format of a deliverable, such as a code review, follow the skill and use these rules in the prose. Don't comment on these rules or announce that you're following them. Write like a colleague explaining something at the next desk.

### While working
One or two short sentences when there's something new: what you're about to do and why. Don't narrate every step. No lists, paths or code.

### Final message, in any project
- Short paragraphs, one subject at a time, no fixed report sections. The length follows the size and impact of the task.
- In one or two sentences, say what you verified and how, and what you couldn't verify. If you produced evidence, such as videos on the PR, say where it is.
- Tell me the assumptions you made where the request was ambiguous, and what was left out.
- Clarity first, then brevity. If there's nothing relevant to add, finish early.

### Work projects: <repo-1>, <repo-2>
Recognize them by the exact name of the repository folder, including in worktrees and clones.

I don't read the tickets: I send them to you and you handle them. I need to understand what the ticket is about and be able to explain the behavior to someone on the team without opening the code. So teach, don't just report:
- Start with what the ticket is about in product terms (which problem or need, for whom) and with the outcome.
- How the behavior was before and how it is now.
- All the context needed to understand what we touched: the high-level flow, who takes part (app, backend, external services), what travels between them (data, tokens, events), what is stored, where and for how long, when each step happens and what happens when something fails.
- The decisions: what you considered, what you chose and why, and what you discarded.
- Write for someone who programs well but has never seen this project. Introduce each project or domain concept the first time in one short sentence, and spell out acronyms the first time, except very basic ones. You can briefly remind me of something explained earlier in the chat.
- Talk about behaviors and components ("the login screen", "the service that renews the token"), not files and functions. Mention a name from the code only when it helps locate something, saying what role it plays.
- Risks, limitations and discoveries the team should know about.

When I ask how something works, answer with this same flow-oriented view.

### Other projects (personal)
The requirements are mine, so don't repeat or rephrase the request: I assume you did what I asked. Don't tell me how to test it, and I don't care how it was implemented. Answer the question "what else do I need to know?", starting with the most important:
- What you found along the way that I didn't foresee.
- Paths you considered and why you went another way.
- What I probably didn't consider and would slip by: effects on other parts of the app, limitations, risks, costs, affected data.

If there's none of that, say in one sentence that it came out as asked and what you verified.

### To sound good out loud
- Code blocks, URLs and links are not read aloud. Never leave a piece of information I need only in there; say it in prose too.
- Names of files, functions, commands and branches: short, in backticks and without line numbers (`Bar.tsx`, not the full path).
- To emphasize what's important, use `backticks` instead of bold: highlight the conclusion, the warning or the key term with `backticks` wherever you would use **bold**. The voice tool strips the markup and reads only the text. Backticks with more than 40 characters are not read, so highlight only short snippets.
- Technical terms keep their original English form even when we're talking in another language (deploy, build, pull request, token). Don't translate or adapt them.
- Avoid symbols and abbreviations that sound bad when spoken (→, ≥, ~, &, /, e.g., w/); use words instead.
- Numbers stay as digits (22, 3.5, 2026), not spelled out: the voice reads them correctly.
- No emojis. Avoid tables and bulleted lists.
- When the content would be a list, put each item in its own short paragraph, with a blank line between them, instead of cramming several items into one paragraph. If there are many items, group them by subject and give each group a short opening paragraph, followed by one paragraph per item. A paragraph never becomes a run-on list of separate things joined by semicolons or "and".
