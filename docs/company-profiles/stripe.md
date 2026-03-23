# Stripe

Stripe is a privately held financial infrastructure company that builds payment processing APIs and business tools for internet commerce. Founded in 2010 by Irish brothers Patrick Collison (CEO) and John Collison (President) and incubated through Y Combinator, the company is dual-headquartered in South San Francisco and Dublin. As of February 2026, Stripe has approximately 14,400 employees across offices in San Francisco, Dublin, Seattle, Singapore, New York, Chicago, London, Amsterdam, Bangalore, Hyderabad, Berlin, Paris, Tokyo, Toronto, and other cities, plus a designated "Remote" engineering hub established in 2019. The company reached a $159 billion valuation in February 2026 via a tender offer (up from $91.5 billion in February 2025), has raised $9.8 billion total across 24 funding rounds from investors including Andreessen Horowitz, Sequoia Capital, and Thrive Capital, and remains private with no near-term IPO plans as of the founders' February 2025 statement. Stripe was "robustly profitable" in 2025 after returning to profitability in 2024 with $101.9 million in pre-tax profit on $5.12 billion in revenue.

## Products and Business

Stripe's core business is payment processing APIs, but the product surface has expanded substantially. The four main product areas are Payments, Connect (for platforms and marketplaces), Revenue (billing and subscriptions), and Money Management (treasury and issuing). Specific products include Stripe Billing (managing 200M+ active subscriptions), Radar (AI-based fraud prevention), Atlas (incorporation service -- 25% of all Delaware corporations), Treasury, Issuing, and the Optimized Checkout Suite (supporting 135+ currencies and payment methods). The Revenue suite alone is on track for $1 billion in annual run rate in 2026.

Businesses running on Stripe processed $1.9 trillion in total payment volume in 2025, up 34% year-over-year, equivalent to roughly 1.6% of global GDP. The platform powers over 5 million businesses directly or through platforms, including 90% of the Dow Jones Industrial Average and 80% of the Nasdaq 100. Technology companies show the highest adoption -- 80% of the largest US software companies use Stripe. 57% of new customers in 2025 were based outside the US.

Recent strategic bets include stablecoin infrastructure (approximately $400 billion in stablecoin payment volume, doubled year-over-year; acquisition of Bridge for stablecoin orchestration and Privy for programmable wallets in July 2025; launch of Tempo blockchain) and an Agentic Commerce Suite developed in partnership with OpenAI for AI agent-to-API transactions. Stripe shipped 350+ product updates in 2025.

Primary competitors include PayPal (43.4% global online payment processing share vs. Stripe's roughly 21-29%), Adyen (the main enterprise payments rival, publicly traded at approximately $53.5 billion market cap), and Block/Square (strongest in SMB and point-of-sale). Stripe holds approximately 68% of the US e-commerce payment processing technology market.

## Workforce History

Stripe grew rapidly through 2022, reaching approximately 8,000 employees before cutting 14% (around 1,120 people) in November 2022. CEO Patrick Collison's memo to employees acknowledged the company had been "much too optimistic about the internet economy's near-term growth" and allowed "coordination costs to grow and operational inefficiencies to seep in." A smaller round of cuts (approximately 40 positions in recruiting) followed in 2023. In January 2025, Stripe laid off 300 employees (3.5% of workforce) in product, engineering, and operations, while simultaneously stating plans to grow to 10,000 by year-end. As of February 2026, headcount stands at 14,381.

## Engineering and Technical Culture

Stripe operates what is likely the world's largest Ruby codebase at over 20 million lines of code across 150,000 files, with active investment in Java and Go for backend services and ML/AI tooling. The company uses Sorbet (a static type checker for Ruby that Stripe open-sourced) across its entire codebase. Internal tooling is extensive and custom-built: Go (a URL shortener with content indexing), Compass (project management with Slack integration and automated standups), Trailhead (internal documentation mirroring external docs), and Sail (an internal framework). Stripe deployed its core APIs 5,978 times in 2022 -- averaging 16.4 times per day -- and reports API reliability "consistently in excess of 99.999%."

Engineering culture is documented extensively through Stripe's own blog and in a two-part deep dive by The Pragmatic Engineer newsletter. Several distinctive patterns emerge:

**Writing-oriented communication.** Stripe has a strong written culture. CTO David Singleton publishes internal blog posts more than once a month. The company frames this as leverage: "engineers love the leverage good writing provides" because written ideas reach more people than verbal ones. This supports their distributed workforce and Remote hub.

**Engineers as product thinkers.** Stripe historically operated without dedicated product managers, and while PMs now exist, engineers remain involved end-to-end: scoping business requirements, talking to users, collaborating with designers and lawyers. The company uses "friction logging" -- engineers systematically document user experience flows, noting pain points in objective language.

**Measurement-heavy.** Stripe "unapologetically measures everything possible about software development processes and practices." Weekly Ops Reviews examine service health and incident patterns. Safe deployment relies on automated testing, CI pipelines, and gradual monitored rollouts.

**"Engineerication."** Engineering leaders periodically work as individual contributors to experience developer productivity pain points firsthand.

**Dual-track career ladder.** Engineers can move between IC and management tracks from L3 onward.

## Workplace Culture and Employee Experience

Glassdoor reviews (3.7/5 across approximately 1,400+ reviews; 58% would recommend to a friend) and Blind posts paint a picture with notable tensions. Compensation and benefits are rated highest at 4.2-4.3/5, while work-life balance is rated lowest at 3.2/5 (and 2.9/5 specifically among software engineers).

**Pace and intensity.** Multiple reviews describe tight deadlines, fast pace, and pressure to be "always on." Several engineering reviews describe 60+ hour weeks, particularly during product launches. Reviews use language like "relentless" and note "just a lot of work, and very very tight deadlines, very fast paced, and not enough engineers and product managers to do all of it." The company's stated operating principles sign teams up for "bold goals" and aim to "achieve them faster than expected."

**Tenure patterns.** A recurring theme across both Glassdoor and Blind is short average tenure. Multiple reviews describe Stripe as "a leaky bucket when it comes to talent" where the typical stay is "15-24 months before making your next move." Reviews attribute this to limited internal development and a perception that "you're replaceable." Some describe the company as lacking "structure to develop talent from within."

**Politics and team variance.** Experience at Stripe varies significantly by team and org. Reviews describe the culture as "very political" where "everyone looks out for themselves." Management quality is uneven -- some managers are described as "responsive" while others are characterized as "inexperienced" or "unqualified." Blind posts describe management-level roles as "very toxic and political" while IC roles can be more insulated. Multiple reviews mention constant reorganizations.

**Performance management.** Stripe uses stack ranking in its performance appraisal process. Reviews describe this as creating competitive dynamics. Combined with periodic layoffs (2022, 2023, 2025), some reviews describe a "culture of fear" leading to finger-pointing. One review summarizes: "This extraordinarily naive evaluation mechanism, combined with stack ranking and what now appear to be annual layoffs, leads to all the negative consequences you'd immediately imagine."

**Compensation and negotiation.** Compensation is rated highly (4.2-4.3/5). However, multiple reviews note that raises after joining are rare -- "Don't expect a salary raise, you have to be a good negotiator when you join. Leadership is very stingy and they consider that you should be grateful for working for Stripe."

**Smart colleagues.** One of the most consistent positive themes across review platforms is the caliber of coworkers. "People are smart and very humble" appears repeatedly. Even critical reviews typically acknowledge the quality of peers.

**Remote and office.** Stripe's Remote hub is coequal with physical engineering offices, and approximately 22% of engineers are permanently remote. Remote employees in major metro areas receive the same pay as in-office counterparts. New hires receive a $1,000 home office stipend.

## Mission and Domain

Stripe's stated mission is "increase the GDP of the internet." The company frames itself as economic infrastructure -- the plumbing that lets businesses accept payments, manage subscriptions, handle fraud, incorporate, and manage money flows. The product surface has expanded from pure payment processing into banking-as-a-service (Treasury, Issuing), corporate incorporation (Atlas), revenue automation (Billing, Revenue Recognition), and most recently crypto/stablecoin infrastructure and agentic commerce. The customer base spans from Y Combinator startups incorporating through Atlas to 90% of the Dow Jones. The underlying technical challenge is maintaining five-nines reliability on financial infrastructure while shipping frequently across a sprawling product surface.
