# Duolingo

Duolingo (NASDAQ: DUOL) is a publicly traded edtech company and the world's most popular language-learning platform. Founded in 2011 by Luis von Ahn and Severin Hacker -- both out of Carnegie Mellon University -- the company is headquartered in Pittsburgh, PA with additional offices in New York, Seattle, Beijing, and Berlin. As of December 31, 2025, Duolingo had 900 full-time employees. The company IPO'd in July 2021 at $102/share, raising approximately $521 million. Full-year 2025 revenue was $1.04 billion (up 39% year-over-year), with net income of $414 million and adjusted EBITDA margin of 29.8%. Q4 2025 DAUs reached 52.7 million, with 12.2 million paid subscribers. Market cap as of March 2026 is approximately $4.7 billion, down substantially from its all-time high of $540/share in May 2025.

## Founders and Origin

Luis von Ahn is a Guatemalan-born computer scientist known for inventing CAPTCHA and reCAPTCHA (the latter sold to Google in 2009). His co-founder Severin Hacker, a Swiss-born CMU PhD student, serves as CTO. The founding thesis was that high-quality language education should be free and accessible to everyone -- von Ahn has described this as a personal mission rooted in growing up in Guatemala, where access to education was a barrier to economic mobility. Duolingo's initial model paired free language learning with crowdsourced translation services; the translation business was later dropped in favor of subscriptions, advertising, and the Duolingo English Test.

## Products and Business Lines

**Language learning app.** Duolingo's flagship product offers courses in over 40 languages through gamified, bite-sized lessons. The free tier is ad-supported; Super Duolingo (the premium subscription) removes ads and adds features like unlimited hearts, progress tracking, and practice modes. The gamification engine -- streaks, leaderboards, XP, leagues -- drives retention. An internal ML model called "Birdbrain" personalizes exercise difficulty based on individual learner strengths and weaknesses.

**Duolingo Max.** A premium tier that integrates GPT-4-powered features including "Explain My Answer" (grammar explanations) and "Roleplay" (AI conversation practice). This represents Duolingo's most visible consumer AI integration.

**Duolingo English Test (DET).** An online English proficiency exam accepted by over 4,800 programs worldwide (including Columbia, Yale, University of Toronto). Priced at $59 per test, it competes with TOEFL and IELTS. At IPO, DET accounted for roughly 10% of revenue. Test-taker volume increased 2,000% between 2019 and 2020, driven partly by pandemic-era demand for remote testing.

**Math, Music, and Chess.** Duolingo has expanded beyond languages, launching Math and Music courses (now available in dozens of languages) and a Chess course that crossed one million DAUs by the end of Q2 2025. In August 2025, the company acquired NextBeat, a music gaming startup, for $34.5 million. The expansion strategy aims to increase total addressable market by applying Duolingo's gamification engine to new subjects.

## Technology and Engineering

Duolingo's backend was originally Python but the session generator engine was rewritten in Scala (JVM), achieving a 98% reduction in average latency (750ms down to 14ms). The company also supports Kotlin as a first-class backend language. The Android app was migrated to 100% Kotlin. Infrastructure runs on AWS (Elastic Beanstalk, S3). Course data is processed offline and serialized to S3.

AI is central to both the product and internal operations. Beyond Birdbrain and the GPT-4 integration in Duolingo Max, the company uses LLMs to generate course content (claiming 4-5x content production speed with AI assistance), and built an AI agent using Temporal and Codex CLI to automate feature flag removal. Speech synthesis uses Microsoft Cognitive Services.

The engineering organization is relatively small for a company of Duolingo's scale -- reportedly 40-50 engineers organized into product teams (learning, growth, foundational/infrastructure). The company's engineering blog (blog.duolingo.com) publishes regularly on topics including ML model development, Scala migration, Kotlin adoption, and internal tooling. The interview process includes a technical assessment, onsite interviews covering coding and system design, a skip-level with the Head of Engineering, and cultural fit evaluation. Duolingo describes itself as a "metrics-based company" where decisions are driven by data and experimentation.

## AI-First Strategy

In April 2025, CEO von Ahn announced Duolingo would become an "AI-first" company. The most concrete manifestation: the company will "gradually stop using contractors to do work that AI can handle" and new headcount will only be approved "if a team cannot automate more of their work." Duolingo had already cut 10% of its contractor workforce in January 2024. Von Ahn has stated that no full-time employees have been laid off -- the company claims to have never laid off a full-time employee since its 2011 founding -- and that AI is making existing employees "four or five times" more productive. The announcement generated significant press attention and some controversy; von Ahn later acknowledged his internal memo "did not give enough context."

In February 2026, Duolingo signaled a strategic shift from prioritizing financial growth to user growth, with a stated goal of reaching 100 million DAUs (roughly double Q4 2025 levels). 2026 guidance calls for 15-18% revenue growth and approximately 25% adjusted EBITDA margin.

## Competitors

In language learning: Babbel (structured, grammar-focused lessons), Rosetta Stone (immersion-based, strong institutional/military adoption), and various smaller apps. Duolingo dominates on accessibility (free tier), scale (50M+ DAUs), and brand recognition. The DET competes with ETS (TOEFL) and the British Council (IELTS) in English proficiency testing. The Math/Music/Chess expansion puts Duolingo in a broader edtech competitive landscape.

## Workplace and Culture

Glassdoor reviews (4.1/5 across approximately 157 reviews, 73% recommend to a friend) and Blind reviews (4.1/5 across 59 reviews) describe a workplace with strong cultural identity and real tension between mission and intensity.

**Mission and people.** The most consistent positive signal across review platforms is that employees are genuinely mission-driven and kind. Multiple reviews describe colleagues as "passionate," "smart," and "genuinely kind," with several attributing this to the company's deliberate hiring for mission alignment. Von Ahn has stated he would rather leave positions unfilled than hire someone who doesn't fit ("it's better to have a hole than an asshole"). The company maintains rituals like a mandatory 12:30-1:30pm lunch hour where meetings pause and everyone eats together.

**Decision-making.** Duolingo is explicitly founder-led. Von Ahn describes having "a view of everything" and uses the phrase "if we're going to go by opinion, let's go by mine." He characterizes the company as "metrics-based" where most decisions rely on data rather than opinion, but when disagreements arise, the founder's view prevails. Von Ahn has described his own evolution from micromanager (up to about 50 employees) to focusing on culture and strategic decisions at the current 900-person scale.

**Pace and intensity.** Reviews consistently describe high expectations around shipping speed. Glassdoor reviews note that "shipping faster is rewarded, which creates the incentive to work longer hours." Multiple reviews mention burnout as a recurring issue, with one pattern being that "many team members have neared or gotten to burnout" and the company "is not investing in team growth as they should." Work-life balance rated 3.6/5 on Glassdoor. The experience appears to vary significantly by team -- some reviews describe sustainable pace while others describe sustained intensity.

**Growth and mobility.** Career opportunities rated 3.6/5 on Glassdoor. A recurring criticism is limited internal mobility -- "no internal mobility for most teams, really hard to grow here." The promotion/raise cycle was reduced to once per year. Some reviews describe clear growth paths for high performers while others describe capped progression.

**Culture fit.** Several reviews note the culture can feel "cliquey, especially if you are not 100% into it." The company's strong cultural identity -- quirky, fun, mission-driven -- reads as genuine to those who resonate with it and exclusionary to those who don't. One Glassdoor review title captures this duality: "The greatest company I never want to work for again."

**Compensation.** Blind data shows total comp ranging from approximately $179K (25th percentile) to $335K (90th percentile) across the US, with Pittsburgh-based roles on the lower end ($178K-$269K). Some senior/NYC offers reach $440K+. Reviews on Blind describe compensation as "below-market compared to similar roles at peers" with "compensation growth not charming over time."

**Work arrangement.** Duolingo uses a hybrid model: in-office Tuesday through Thursday, optional remote Monday and Friday. The company implemented return-to-office in 2022, having told employees during the initial shift to remote that it would not be permanent. Von Ahn has said he does not have the "political power" to change the current hybrid arrangement further. Duolingo has expanded its Pittsburgh headquarters to accommodate growth.
