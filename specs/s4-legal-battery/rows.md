# Legal battery rows (orchestrator's gold; verify, do not change)

Format: id | split | question | instrument | must_reach | should_reach | answer_checks

- L01 | dev | What is the limitation period for bringing a claim in tort under the Limitation Act 1980? | Limitation Act 1980 | s.2 | s.11, s.4A, s.1 | required_citations s.2; credit_citations s.11, s.4A, s.14A, s.14B, s.28, s.32, s.33, s.1(2)
- L02 | test | How long do I have to sue for breach of a simple contract under the Limitation Act 1980? | Limitation Act 1980 | s.5 | s.8 | null
- L03 | dev | What is the time limit for a personal injury claim under the Limitation Act 1980? | Limitation Act 1980 | s.11 | s.14, s.33 | null
- L04 | test | Under the Limitation Act 1980, how long do I have to bring a defamation claim? | Limitation Act 1980 | s.4A | s.32A | null
- L05 | dev | Under the Limitation Act 1980, when does time run for negligence where the damage was latent? | Limitation Act 1980 | s.14A | s.14B | null
- L06 | test | Under the Companies Act 2006, what is a director's duty to promote the success of the company? | Companies Act 2006 | s.172 | | null
- L07 | dev | Quote the exact statutory wording of section 172(1)(a) to (d) of the Companies Act 2006. | Companies Act 2006 | s.172(1)(a), s.172(1)(b), s.172(1)(c), s.172(1)(d) | | required_citations s.172(1)(a), s.172(1)(b), s.172(1)(c), s.172(1)(d)
- L08 | test | Under the Companies Act 2006, what must a director do about conflicts of interest? | Companies Act 2006 | s.175 | s.177 | null
- L09 | dev | How long is the short-term right to reject under the Consumer Rights Act 2015? | Consumer Rights Act 2015 | s.22 | s.20, s.24 | required_citations s.22
- L10 | test | Under the Consumer Rights Act 2015, must goods be of satisfactory quality? | Consumer Rights Act 2015 | s.9 | | null
- L11 | dev | What is the limit of the compensatory award under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.124 | s.123 | null
- L12 | test | How long must I have worked to claim unfair dismissal under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.108 | | null
- L13 | dev | What minimum notice must an employer give under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.86 | | null
- L14 | test | What is the time limit for presenting an unfair dismissal complaint under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.111 | | null
- L15 | dev | What is the duty to make reasonable adjustments under the Equality Act 2010? | Equality Act 2010 | s.20 | s.21 | null
- L16 | test | What are the protected characteristics in the Equality Act 2010? | Equality Act 2010 | s.4 | | null
- L17 | dev | What counts as direct discrimination under the Equality Act 2010? | Equality Act 2010 | s.13 | | null
- L18 | test | How does the Theft Act 1968 define theft? | Theft Act 1968 | s.1 | | null
- L19 | dev | When is appropriation not dishonest under the Theft Act 1968? | Theft Act 1968 | s.2 | | null
- L20 | test | Under the Housing Act 1988, how does a landlord recover possession when an assured shorthold tenancy ends? | Housing Act 1988 | s.21 | | null
- L21 | dev | What repairs must a landlord carry out under the Landlord and Tenant Act 1985 for a short lease of a dwelling? | Landlord and Tenant Act 1985 | s.11 | | null
- L22 | test | What does the Sale of Goods Act 1979 say about satisfactory quality? | Sale of Goods Act 1979 | s.14 | | null
- L23 | dev | What is the common duty of care under the Occupiers' Liability Act 1957? | Occupiers' Liability Act 1957 | s.2 | | null
- L24 | test | When can I get damages for misrepresentation under the Misrepresentation Act 1967? | Misrepresentation Act 1967 | s.2 | | null
- L25 | dev | Can a business exclude liability for negligence under the Unfair Contract Terms Act 1977? | Unfair Contract Terms Act 1977 | s.2 | | null
- L26 | test | When is a statement defamatory under the Defamation Act 2013? | Defamation Act 2013 | s.1 | | null
- L27 | dev | What is fraud by false representation under the Fraud Act 2006? | Fraud Act 2006 | s.2 | | null
- L28 | test | How must courts interpret legislation under the Human Rights Act 1998? | Human Rights Act 1998 | s.3 | | null
- L29 | dev | When can a court make a declaration of incompatibility under the Human Rights Act 1998? | Human Rights Act 1998 | s.4 | | null
- L30 | test | When is a company unable to pay its debts under the Insolvency Act 1986? | Insolvency Act 1986 | s.123 | | null
- L31 | dev | What is the welfare principle in the Children Act 1989? | Children Act 1989 | s.1 | | null
- L32 | test | What is the offence of dangerous driving in the Road Traffic Act 1988? | Road Traffic Act 1988 | s.2 | s.2A | null
- L33 | dev | What does the Protection from Harassment Act 1997 prohibit? | Protection from Harassment Act 1997 | s.1 | | null
- L34 | test | What general duties do employers owe their employees under the Health and Safety at Work etc. Act 1974? | Health and Safety at Work etc. Act 1974 | s.2 | | null
- L35 | dev | What inventions are patentable under the Patents Act 1977? | Patents Act 1977 | s.1 | | null
- L36 | test | How long does copyright last in a literary work under the Copyright, Designs and Patents Act 1988? | Copyright, Designs and Patents Act 1988 | s.12 | | null
- L37 | dev | When can an arbitration award be challenged for serious irregularity under the Arbitration Act 1996? | Arbitration Act 1996 | s.68 | | null
- L38 | test | What is the offence of failing to prevent bribery under the Bribery Act 2010? | Bribery Act 2010 | s.7 | | null
- L39 | dev | What is unauthorised access to computer material under the Computer Misuse Act 1990? | Computer Misuse Act 1990 | s.1 | | null
- L40 | test | What remedies does the Consumer Rights Act 2015 give when goods do not conform to the contract? | Consumer Rights Act 2015 | s.19 | | null

Every row with answer_checks also gets the R4 defaults (forbidden_phrases, max_abstentions 1, no_citations_on_abstention, no_duplicate_sentences).
