# Cloudflare

Cloudflare, Inc. (NYSE: NET) is a publicly traded internet infrastructure and security company headquartered in San Francisco, California. Founded in 2009 by Matthew Prince (CEO), Michelle Zatlyn (COO), and Lee Holloway, the company IPO'd in September 2019 at $15/share. As of March 2026, Cloudflare has a market capitalization of approximately $70B and a stock price around $225. FY 2025 revenue was $2.17B, up 29.8% year-over-year, with Q4 2025 growth accelerating to 34%. Non-GAAP operating income for 2025 was $303.9M (14% margin) and free cash flow reached $260.6M. The company has approximately 4,300 employees per its most recent annual filing, with some third-party estimates placing the number higher (approaching 5,000-6,800 depending on source and timing) as of early 2026, reflecting ongoing headcount growth.

## Origin and Mission

Cloudflare grew out of Project Honey Pot, a system Prince and Holloway built in 2004 to track how spammers harvested email addresses. Thousands of websites across 185+ countries participated. Users kept asking for the system to not just track bad actors but stop them. Prince met Zatlyn at Harvard Business School in 2009; they recognized the opportunity to build a security-and-performance layer for the entire internet. The company won HBS's business plan competition in 2009, launched publicly at TechCrunch Disrupt in September 2010, and has since grown into what it calls the "Connectivity Cloud" -- a unified global network spanning 300+ cities where every server can perform every function, from CDN delivery to security to AI inference.

## Products and Platform

Cloudflare's product surface is broad and continues to expand through both organic development and acquisitions:

**Core network services.** CDN, DDoS mitigation, DNS, SSL/TLS termination, and web application firewall (WAF). According to W3Techs, approximately 21.3% of all websites on the internet use Cloudflare as of January 2026.

**Zero Trust / SASE.** Cloudflare One provides identity-aware access controls, secure web gateway, and network-level security. The company positions this against Zscaler and Palo Alto Networks for enterprise network security.

**Developer Platform.** Cloudflare Workers (edge compute), R2 (object storage with zero egress fees, positioned against AWS S3), D1 (edge database), KV (key-value store), Durable Objects (stateful edge compute), Queues, and Vectorize (vector database). The developer platform is the fastest-growing segment. Cloudflare Containers launched in late 2025, enabling Dockerized workloads at the edge.

**AI inference.** Workers AI runs 50+ open-source models across 200+ cities with serverless pricing and an OpenAI-compatible API. Cloudflare built its own LLM inference engine in Rust called Infire. The strategic bet is that enterprises will shift to open-source models as agent-driven workloads scale, and Cloudflare wants to be the infrastructure layer for that shift. AI Gateway provides unified billing, routing, and observability for AI applications.

**Recent acquisitions** include Area 1 Security (email security, 2022), Nefeli Networks (multi-cloud networking, March 2024), Replicate (ML model deployment platform, November 2025), and The Astro Technology Company (the Astro web framework, January 2026).

## Technology and Engineering

Cloudflare is a Rust-heavy engineering organization. The company is in the process of rewriting its core forwarding layer from C++ to Rust (the project called FL2, built on their Oxy proxy framework), reporting a 40% reduction in CPU usage. Customer traffic began flowing through FL2 in early 2025, with the legacy FL1 system scheduled for shutdown in early 2026. Their HTTP/TLS termination layer (previously NGINX) is also being rewritten in Rust.

Notable open-source projects include Pingora (a Rust framework for programmable network services), Foundations (a Rust service foundation library), and ecdysis (zero-downtime upgrade library). The Workers runtime uses V8 and WebAssembly, supporting Rust, JavaScript/TypeScript, Python, and other languages at the edge. The engineering blog is well-regarded in the industry -- Dan Luu has noted its high proportion of technical deep-dive posts, and multiple employees describe it as a recruiting pipeline unto itself.

## Competitors

Cloudflare competes across multiple categories. In CDN and edge delivery: Akamai, Fastly, Amazon CloudFront. In Zero Trust/SASE: Zscaler, Palo Alto Networks. In developer platform and edge compute: AWS Lambda@Edge, Vercel, Deno Deploy, Fastly Compute. In AI inference: AWS Bedrock, Google Cloud, Azure OpenAI, Replicate (now owned by Cloudflare). The company differentiates by having built its entire stack natively on one control plane and one network, rather than assembling acquisitions.

## Compensation

Per Levels.fyi, median total compensation for software engineers is approximately $196K, ranging from roughly $141K to $326K+. The RSU vesting schedule is standard 4-year with 25% annual cliff. Employee reviews on Blind and Glassdoor consistently note that base salary is below market for comparable companies, with equity forming a larger portion of total comp. RSU refreshes reportedly come every 2 years. There is no 401(k) match -- this is a recurring complaint. The engineering level system is described as relatively flat, with most engineers at L3-L4, few L5s, and very few L6s across the company.

## Workplace and Culture

Cloudflare has offices globally with positions listed as hybrid, distributed, or in-office across the Americas, Europe, and Asia. The company describes itself as committed to flexible working arrangements, though specific policies vary by team.

Glassdoor reviews (3.4/5 across approximately 936 reviews, with 52% recommending to a friend) and Blind reviews (3.6/5 across 333 reviews) paint a picture of a company in cultural transition. Several themes emerge consistently:

**Talented colleagues, strong technical work.** Across both engineering and sales reviews, the most consistent positive is the quality of coworkers. People describe "smart and talented colleagues," "amazing engineering challenges," and a historically "blameless culture" in engineering. The products themselves are widely respected -- multiple reviews note pride in what Cloudflare builds and its role in internet infrastructure.

**A culture shifting from engineering-driven to sales-driven.** Multiple Blind reviews from 2024-2025 describe a transition: "used to be an engineer-driven organization, but not anymore." Reviews attribute this to new executive and middle management hires from Salesforce, Cisco, and similar enterprise companies who are described as "attempting to replicate their previous company culture." This is a source of friction -- several reviews describe it as "crushing the culture and morale of many people across many teams."

**Frequent reorganizations.** Glassdoor and Blind reviews consistently mention regular reorganizations, particularly in the sales organization, which underwent a major restructuring in early 2025 including demotions of BDR team leads and AE managers. The cybersecurity division reportedly had hiring on hold due to ongoing reorg. Reviews describe "lots of conflicting guidance from senior leadership" and priorities that shift frequently.

**Talent attrition and understaffing.** Several reviews note that "a huge chunk of the best talent has left over the last 2-3 years," leaving remaining experienced engineers "overworked and burning out." Teams are described as "severely understaffed" partly due to hiring pipeline challenges. High turnover is mentioned as creating instability.

**Heavy meeting culture.** Engineering reviews describe what one called "the heaviest meeting culture" they had experienced, described as "literally insane." This contrasts with the company's earlier reputation as an engineering-focused, builder-oriented culture.

**Manager-dependent experience.** A recurring theme: "your level manager dictates your experience here." Reviews describe wide variance in management quality, with some managers praised as excellent and others criticized for credit-taking, favoritism, and information withholding. Promotions are described as difficult to achieve.

**Sales organization specifically.** The sales side appears to have experienced more disruption than engineering. Reviews describe post-sales support as "non-existent," customer success teams "constantly shrinking," and pressure on account managers and solutions engineers to fill gaps. The 2024 layoffs disproportionately affected BDRs and the mid-market sales team.

**CEO perception.** Matthew Prince has a 71/100 approval rating on Comparably (top 35% for similarly-sized companies). He is described externally as a strong storyteller and personally engaged leader. Employee sentiment is more mixed -- longer-tenured employees and the sales department rate him less favorably, per Comparably data.

Cloudflare uses the formula "PERFORMANCE = RESULTS + BEHAVIORS" in its internal performance framework, emphasizing both outcomes and how work gets done.

## Summary Context

Cloudflare is a large, publicly traded infrastructure company with a strong technical reputation, a broad and growing product portfolio, and a mission tied to making the internet faster and more secure. It occupies an unusual position -- a company building critical internet infrastructure that also operates a developer platform competing with hyperscalers. The engineering work is technically deep (systems programming in Rust, global-scale distributed systems, AI inference at the edge), but the organizational experience varies significantly by team, manager, and function. The company is growing quickly in revenue and headcount, and the cultural growing pains that come with that transition -- from scrappy engineering-driven startup to enterprise sales organization -- are a dominant theme in employee reviews from 2024-2025.
