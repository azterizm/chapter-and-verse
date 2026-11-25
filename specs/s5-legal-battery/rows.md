# Legal battery rows L41 to L80 (orchestrator's gold; verify, do not change)

Format: id | split | question | instrument(s) | must_reach | should_reach | answer_checks

"none" in must_reach: a "not in the Act" row. The named instrument does not answer the question; the right answer is the abstention. `expect_abstention` and row-specific `forbidden_phrases` (added to the defaults) apply.

- L41 | dev | Under the Working Time Regulations 1998, how much annual leave is a worker entitled to? | Working Time Regulations 1998 | reg 13, reg 13A | | required reg 13, reg 13A
- L42 | test | What is the maximum average weekly working time under the Working Time Regulations 1998? | Working Time Regulations 1998 | reg 4 | reg 5 | required reg 4
- L43 | dev | Under the Working Time Regulations 1998, can a worker agree to work more than 48 hours a week? | Working Time Regulations 1998 | reg 4, reg 5 | | required reg 4, reg 5
- L44 | test | What rest break is an adult worker entitled to during the working day under the Working Time Regulations 1998? | Working Time Regulations 1998 | reg 12 | reg 10 | null
- L45 | dev | How long is the cancellation period for an online purchase under the Consumer Contracts (Information, Cancellation and Additional Charges) Regulations 2013? | Consumer Contracts (Information, Cancellation and Additional Charges) Regulations 2013 | reg 30 | reg 29, reg 31 | required reg 30; credit reg 29, reg 31
- L46 | test | Under the Consumer Contracts (Information, Cancellation and Additional Charges) Regulations 2013, how quickly must a trader refund a consumer who cancels? | Consumer Contracts (Information, Cancellation and Additional Charges) Regulations 2013 | reg 34 | | null
- L47 | dev | Under the Transfer of Undertakings (Protection of Employment) Regulations 2006, what happens to employees' contracts when a business is transferred? | Transfer of Undertakings (Protection of Employment) Regulations 2006 | reg 4 | reg 3 | required reg 4
- L48 | test | Is a dismissal because of the transfer automatically unfair under the Transfer of Undertakings (Protection of Employment) Regulations 2006? | Transfer of Undertakings (Protection of Employment) Regulations 2006 | reg 7 | | required reg 7
- L49 | dev | What must an employer's risk assessment cover under the Management of Health and Safety at Work Regulations 1999? | Management of Health and Safety at Work Regulations 1999 | reg 3 | | required reg 3
- L50 | test | Under the Housing Act 2004, within what time must a landlord protect a tenancy deposit? | Housing Act 2004 | s.213 | s.214 | null
- L51 | dev | Can a business exclude liability for death or personal injury caused by negligence under the Unfair Contract Terms Act 1977, and what about other loss or damage? | Unfair Contract Terms Act 1977 | s.2(1), s.2(2) | s.11 | required s.2(1), s.2(2); credit s.11
- L52 | test | Under the Consumer Rights Act 2015, can a trader exclude liability for death or personal injury caused by negligence? | Consumer Rights Act 2015 | s.65 | s.62 | required s.65
- L53 | dev | Under the Consumer Rights Act 2015, which contract terms are exempt from the fairness assessment? | Consumer Rights Act 2015 | s.64 | s.62 | required s.64
- L54 | test | Under the Employment Rights Act 1996, how long must an employee have worked to claim a redundancy payment? | Employment Rights Act 1996 | s.155 | s.135 | null
- L55 | dev | Under the Employment Rights Act 1996, is a dismissal for making a protected disclosure automatically unfair, and does the qualifying period of employment apply? | Employment Rights Act 1996 | s.103A, s.108 | s.43B | required s.103A, s.108
- L56 | test | What is a qualifying disclosure under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.43B | | null
- L57 | dev | What is the time limit for a complaint of unauthorised deductions from wages under the Employment Rights Act 1996? | Employment Rights Act 1996 | s.23 | s.13 | required s.23
- L58 | test | What counts as harassment under the Equality Act 2010? | Equality Act 2010 | s.26 | | required s.26
- L59 | dev | What is indirect discrimination under the Equality Act 2010? | Equality Act 2010 | s.19 | | required s.19
- L60 | test | When does a person have a disability under the Equality Act 2010? | Equality Act 2010 | s.6 | Sch 1 para 2 | null
- L61 | dev | Quote the exact wording of section 1 of the Theft Act 1968. | Theft Act 1968 | s.1 | | required s.1
- L62 | test | Quote section 2(1) of the Fraud Act 2006. | Fraud Act 2006 | s.2(1) | | required s.2(1)
- L63 | dev | Quote the exact words of section 174 of the Companies Act 2006. | Companies Act 2006 | s.174 | | required s.174
- L64 | test | Quote section 11(1) of the Landlord and Tenant Act 1985. | Landlord and Tenant Act 1985 | s.11(1) | | null
- L65 | dev | Under the Limitation Act 1980, when is the limitation period postponed for fraud, concealment or mistake? | Limitation Act 1980 | s.32 | | required s.32
- L66 | test | How long do I have to bring an action to recover land under the Limitation Act 1980? | Limitation Act 1980 | s.15 | | required s.15
- L67 | dev | Under the Limitation Act 1980, can the court allow a personal injury claim brought after the time limit? | Limitation Act 1980 | s.33 | s.11 | required s.33; credit s.11
- L68 | test | Under the Insolvency Act 1986, when is a director liable for wrongful trading? | Insolvency Act 1986 | s.214 | | null
- L69 | dev | What is a transaction at an undervalue under the Insolvency Act 1986? | Insolvency Act 1986 | s.238 | s.240 | required s.238; credit s.240
- L70 | test | Under the Police and Criminal Evidence Act 1984, when can a court exclude evidence? | Police and Criminal Evidence Act 1984 | s.78 | s.76 | null
- L71 | dev | Under the Police and Criminal Evidence Act 1984, does a person held in police detention have a right to legal advice? | Police and Criminal Evidence Act 1984 | s.58 | | required s.58
- L72 | test | What is the general right of access to information under the Freedom of Information Act 2000? | Freedom of Information Act 2000 | s.1 | s.10 | null
- L73 | dev | Within how many working days must a public authority answer a request under the Freedom of Information Act 2000? | Freedom of Information Act 2000 | s.10 | | required s.10
- L74 | test | How long do I have to bring a claim against a public authority under the Human Rights Act 1998? | Human Rights Act 1998 | s.7 | | null
- L75 | dev | What is the national minimum wage hourly rate under the Employment Rights Act 1996? | Employment Rights Act 1996 | none | | expect_abstention; forbidden_phrases "£", "per hour"
- L76 | test | Under the Theft Act 1968, what is the time limit for bringing a civil claim for the value of stolen goods? | Theft Act 1968 | none | | expect_abstention; forbidden_phrases "six years", "years from"
- L77 | dev | Under the Consumer Rights Act 2015, how long is the cooling-off period for buying goods online? | Consumer Rights Act 2015 | none | | expect_abstention; forbidden_phrases "14 days", "fourteen days"
- L78 | test | What is the speed limit on a motorway under the Road Traffic Act 1988? | Road Traffic Act 1988 | none | | expect_abstention; forbidden_phrases "70", "miles per hour", "mph"
- L79 | dev | Under the Consumer Rights Act 2015 and the Unfair Contract Terms Act 1977, which Act governs a term excluding a trader's liability to a consumer for negligence causing personal injury? | Consumer Rights Act 2015; also Unfair Contract Terms Act 1977 | CRA s.65 | UCTA s.2 | required CRA s.65; credit UCTA s.2
- L80 | test | Under the Sale of Goods Act 1979 and the Consumer Rights Act 2015, which applies to a business-to-business sale of goods that are not of satisfactory quality? | Sale of Goods Act 1979; also Consumer Rights Act 2015 | SGA s.14 | CRA s.9 | required SGA s.14

Every row with answer_checks also gets the defaults (forbidden_phrases, max_abstentions 1, no_citations_on_abstention, no_duplicate_sentences). Expected weak spots, still gold: multi-instrument rows (L79, L80) and the "none" rows may fail today; that is what the battery is for.
