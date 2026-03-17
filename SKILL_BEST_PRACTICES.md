# Best Practices for Building Claude Code Skills

Lessons learned from building the [python-workers](https://github.com/adewale/python-workers-skill) skill, combined with conventions observed across the skill ecosystem.

## Repo Structure

Separate the installable skill from repo metadata. The `skill/` directory is what gets installed; everything else is for humans.

```
my-skill-repo/
├── .claude-plugin/
│   └── marketplace.json        # For marketplace distribution
├── .gitignore
├── README.md                   # Repo README (not part of skill)
├── LICENSE
├── skill/                      # The installable skill
│   ├── SKILL.md                # Required: frontmatter + instructions
│   ├── references/             # Docs loaded on demand
│   └── scripts/                # Executable code
└── tests/                      # Evals/benchmarks (not part of skill)
```

**Why**: When a user installs the skill, they get `skill/` — not your README, LICENSE, or test harness. Keeping them separate avoids polluting the installed skill with repo metadata.

**Sources**: [blacktop/ipsw-skill](https://github.com/blacktop/ipsw-skill), [solana-foundation/solana-dev-skill](https://github.com/solana-foundation/solana-dev-skill), [anthropics/skills](https://github.com/anthropics/skills)

## SKILL.md

### Keep it under 500 lines

SKILL.md is loaded into context whenever the skill triggers. Every line costs tokens on every invocation. Put detailed reference material in `references/` files and point to them from SKILL.md.

**Source**: [Agent Skills Specification](https://agentskills.io/specification)

### Use decision trees, not walls of text

Decision trees let the model (and humans) quickly navigate to the right reference file for their situation. They're more effective than long prose instructions.

```
Which binding?
├─ D1 (SQL database)    -> api.md (D1 section)
├─ KV (key-value)       -> api.md (KV section)
└─ Something isn't working -> gotchas.md
```

**Source**: Learned building python-workers — the decision trees in SKILL.md are the primary navigation mechanism.

### Include anti-patterns with code examples

Show the wrong way AND the right way. Models learn from contrast more effectively than from instructions alone.

```python
# NEVER — creates JS Map, most APIs reject it
js_obj = to_js({"topK": 50})

# ALWAYS — creates JS Object
js_obj = to_js({"topK": 50}, dict_converter=Object.fromEntries)
```

**Source**: Learned building python-workers — anti-patterns in SKILL.md were consistently applied by the model in eval runs.

### Make the description "pushy"

The description field is the primary trigger mechanism. Claude tends to under-trigger skills, so be explicit about when to use it. Include specific contexts, not just what it does.

**Source**: [skill-creator skill](https://github.com/anthropics/skills) documentation.

## References

### Organize by what the user is trying to do

Don't organize by internal structure. Group content by task:
- `api.md` — "I need to call an API"
- `configuration.md` — "I need to configure something"
- `gotchas.md` — "Something isn't working"
- `patterns.md` — "I need a production pattern for X"

**Source**: Learned building python-workers — reorganized from file-structure-based to task-based after reviewing other skills.

### Include a table of contents for files over 300 lines

Large reference files need a TOC so the model can navigate to the right section without reading the whole file.

**Source**: [Agent Skills Specification](https://agentskills.io/specification)

### Keep code examples — they're the most valuable part

Reference files should be heavy on code examples with brief explanations. Models apply code patterns more reliably than prose instructions.

**Source**: Eval results from python-workers — the model consistently reproduced code patterns from the skill (HAS_PYODIDE guard, boundary layer, mock implementations) verbatim.

## Naming Conventions

| Directory | Purpose | Convention |
|-----------|---------|-----------|
| `references/` | Docs loaded on demand | Most common (spec standard) |
| `reference/` | Same, singular | Used by some (anthropics/skills, gapmiss) |
| `scripts/` | Executable code | Universal |
| `assets/` | Templates, images, fonts | Spec standard, but often domain-specific names |
| `resources/` | Alternative to references | Used by trailofbits |

**Source**: Survey of [anthropics/skills](https://github.com/anthropics/skills), [trailofbits/skills](https://github.com/trailofbits/skills), [microsoft/skills](https://github.com/microsoft/skills), [blacktop/ipsw-skill](https://github.com/blacktop/ipsw-skill), [gapmiss/obsidian-plugin-skill](https://github.com/gapmiss/obsidian-plugin-skill)

## Testing & Evals

### Assertions need to discriminate

If both with-skill and without-skill runs pass all assertions, the assertions aren't testing what the skill uniquely adds. Check for specific details, exact patterns, and edge case awareness — not just pattern presence.

**Source**: Learned building python-workers — iteration 1 evals showed 100% pass rate for both with-skill and baseline, revealing non-discriminating assertions.

### Run with-skill and baseline in parallel

Always compare against a baseline (no skill) to measure what the skill actually adds. Launch both runs simultaneously so they finish around the same time.

**Source**: [skill-creator skill](https://github.com/anthropics/skills) documentation.

### Qualitative review matters more than pass rates

The eval viewer lets you compare outputs side-by-side. Often the with-skill output is qualitatively better (more nuanced, fewer subtle errors) even when both pass the same assertions.

**Source**: Learned building python-workers — quantitative pass rates were identical but qualitative differences were visible in the viewer.

## Gathering Source Material

### Start from production experience

The most valuable skill content comes from hard-won lessons — bugs encountered in production, patterns that emerged over multiple projects, gotchas that documentation doesn't cover.

**Source**: python-workers skill was built from [planet_cf](https://github.com/adewale/planet_cf) and [tasche](https://github.com/adewale/tasche) LESSONS_LEARNED files.

### Cross-reference official docs

After drafting from experience, compare against official documentation. This catches:
- Incorrect API signatures (e.g., `scheduled()` handler had wrong parameter count)
- Outdated patterns (e.g., Workflow step.do() API had changed)
- Missing features the official docs cover

**Source**: Learned building python-workers — comparing against [Cloudflare Python Workers docs](https://developers.cloudflare.com/workers/languages/python/) found 2 significant corrections.

### Study official examples repos

Official example repos reveal patterns that documentation doesn't always cover — real wrangler.jsonc configs, package compatibility, which libraries actually work on the platform.

**Source**: [cloudflare/python-workers-examples](https://github.com/cloudflare/python-workers-examples) revealed 12 new patterns including Workflows API, DO alarms, binary response patterns, and confirmed-working package list.

### Create a human-readable mirror

A best practices doc that mirrors the skill's content makes it easier for non-technical reviewers to give feedback. Changes to the doc map directly to changes in the skill.

**Source**: Learned building python-workers — BEST_PRACTICES.md serves as the review surface for the skill.

## Distribution

### Include marketplace.json for Claude Code

```json
{
  "name": "my-skill",
  "description": "What it does",
  "skills": ["skill"]
}
```

Place in `.claude-plugin/marketplace.json` at the repo root.

**Source**: [anthropics/skills](https://github.com/anthropics/skills), [blacktop/ipsw-skill](https://github.com/blacktop/ipsw-skill), [trailofbits/skills](https://github.com/trailofbits/skills)

### Add a .gitignore

At minimum, exclude:
```
.DS_Store
__pycache__/
*.pyc
.claude/
.env
python-workers-workspace/
```

**Source**: Survey of skill repos — most include a .gitignore excluding local Claude state and build artifacts.
