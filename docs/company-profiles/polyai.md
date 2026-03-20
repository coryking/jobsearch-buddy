# PolyAI

PolyAI is a venture-backed voice AI company that builds conversational agents for enterprise contact centers. Founded in 2017 as a spin-out from the University of Cambridge's Machine Intelligence Lab, the company is headquartered in London with offices in New York (1178 Broadway), San Mateo (California), and Belgrade (Serbia). As of early 2026, PolyAI has approximately 300-350 employees across the UK, US, Serbia, Canada, and the Philippines. The company raised an $86 million Series D in December 2025 co-led by Georgian, Hedosophia, and Khosla Ventures, with participation from NVentures (NVIDIA's venture capital arm), Citi Ventures, Zendesk Ventures, Sands Capital, and Point72 Ventures. That round valued PolyAI at $750 million (up from approximately $500 million at its Series C in mid-2024) and brought total funding above $200 million. ARR approximately doubled to $40 million in 2025, with U.S. revenue nearly tripling.

## Founders and Origin

The three co-founders -- Nikola Mrksic (CEO), Tsung-Hsien "Shawn" Wen (CTO), and Pei-Hao "Eddy" Su (SVP Engineering) -- all completed PhDs at Cambridge, working on dialogue systems and spoken language understanding. Before founding PolyAI, Mrksic was an early employee at VocalIQ, a Cambridge voice tech startup acquired by Apple in 2015 to improve Siri; he worked at Apple for two years before leaving to start PolyAI. Wen and Su had stints at Google and Facebook AI Research respectively. Mrksic was named to Forbes 30 Under 30. The founding thesis was that enterprise voice assistants could be built to handle real conversations rather than forcing callers through rigid IVR menus.

## Product and Technology

PolyAI's core product is a voice agent platform for inbound customer service calls. The company launched Agent Studio in April 2025 as its voice-first, omnichannel platform for building and managing enterprise-grade conversational AI agents. The platform handles authentication, order management, billing, reservations, and other high-volume interactions, with the company claiming its agents can resolve over 50% of customer inquiries without human handoff.

The technology stack is substantially proprietary. PolyAI built its own automatic speech recognition engine (called Owl ASR), designed for noisy real-world call environments with accent variation. The company also developed its own LLM called Raven, trained on billions of conversations and optimized for sub-second response times and adherence to business rules rather than general-purpose text generation. Voice synthesis uses a hybrid approach blending human recordings with neural synthesis. The company holds multiple granted patents with additional applications pending, and its academic research has been cited over 12,000 times. On GitHub (github.com/PolyAI-LDN), the most notable open-source contribution is the "conversational-datasets" repository (1,400+ stars), which provides large-scale datasets for training conversational AI models.

## Customers and Market

PolyAI targets mid-sized and large enterprises in hospitality, healthcare, financial services, gaming, energy, and retail. Named customers include Marriott, Caesars Entertainment, FedEx, PG&E, UniCredit, and Foot Locker. The company reports 100+ enterprise customers with over 2,000 live deployments across 45 languages and 25+ countries. A 2025 Forrester Total Economic Impact study commissioned by PolyAI reported 391% ROI for customers, with average savings of $10.3 million. The company integrates with contact center platforms via SIP/PSTN and partners with Twilio for deployment.

Competitors in the enterprise voice AI space include Replicant, Cognigy, Google Dialogflow, Amazon Lex, SoundHound (Amelia), Retell AI, and Sierra. PolyAI differentiates primarily on owning the full stack (ASR, LLM, voice synthesis) rather than assembling third-party components, and on voice-first design rather than adapting chatbot technology to voice.

## Engineering Culture

PolyAI operates with "forward-deployed" engineering teams that work directly with clients -- engineers visit call centers, listen to real conversations, and collaborate with interaction specialists and agent designers before building solutions. The engineering blog describes a pragmatic, user-focused approach: when email collection over poor phone connections creates friction, engineers recommend collecting phone numbers instead and doing CRM lookups rather than forcing the technically "correct" solution. Latency optimization is a core engineering concern, with teams targeting approximately one-second end-to-end response times across the full pipeline (ASR, intent processing, API calls, response generation, TTS).

Current open roles (16 as of March 2026) span engineering, sales, customer success, and product marketing across London, New York, the San Francisco Bay Area, Serbia, Canada, and the broader US and UK. Job listings indicate a hybrid model -- the New York senior full-stack engineer role specifies "Hybrid - Manhattan based 1-2x week," while several UK roles require being based in the UK without specifying office days. Some roles are explicitly location-constrained (Serbia, PST timezone for "Forward Deployed AI Engineer").

## Workplace and Culture

Glassdoor reviews (4.8/5 across approximately 90 reviews, with 92% recommending to a friend and 89% positive business outlook) paint a consistently positive picture, though the relatively small review count means individual reviews carry more weight.

**Pace and growth stage.** Reviews describe PolyAI as being in a "strong growth stage" with "speed and agility." One review titled "Moving Fast and Furious" captures the tempo. Multiple reviews note that onboarding can be difficult because priorities shift and processes are still being built -- a pattern consistent with a company that has roughly doubled in headcount and revenue over the past year.

**People and collaboration.** A recurring theme across reviews is the quality of colleagues -- "smart people to learn from," "driven and dedicated," "low ego" environment. Several reviews highlight that collaboration and transparency extend to the executive team, with the CEO described as someone who "genuinely cares." The company is described as having a welcoming community across its globally distributed hubs.

**Operational growing pains.** The most specific criticism pattern involves internal tooling and processes lagging behind growth. Multiple reviews mention poor Salesforce data quality, with employees "wasting valuable time manually organizing spreadsheets because reporting is inaccurate." This suggests the company is at the stage where scrappy early-stage practices are colliding with the operational needs of a 300+ person organization serving 100+ enterprise customers.

**Flexibility and benefits.** Reviews mention autonomy on projects, flexible work-from-home culture, good benefits, and regular team socials. The careers page describes employees as able to choose between office, home, traveling, or a combination.

**Work content.** One review titled "Cool company, boring work" suggests that not all roles involve cutting-edge technical work -- a common pattern at AI companies where much of the day-to-day involves integration, deployment, and customer-specific configuration rather than research.
