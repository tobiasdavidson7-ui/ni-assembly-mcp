"""Declarative form specs for the no-LLM public UI (PLAN.md Phase 10 commit 2).

Each :class:`FormSpec` binds a small HTML form to one **already-registered** MCP
tool function (:data:`ni_assembly_mcp.tools.ALL_TOOLS`). The view layer
(:mod:`ni_assembly_mcp.forms.views`) renders the fields, coerces the submitted
strings and calls ``spec.tool(**kwargs)`` directly — there is no model in the
loop and no way to reach anything that is not in this list.

The offline index builders (``ni-assembly-mcp index …``) are a CLI subcommand,
never tool functions, so they are unreachable from HTTP by construction;
``tests/test_forms.py`` pins that every ``spec.tool`` is one of ``ALL_TOOLS``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ni_assembly_mcp.tools import ALL_TOOLS
from ni_assembly_mcp.tools.committees import get_committee_agenda
from ni_assembly_mcp.tools.hansard import (
    find_relevant_contributors,
    get_hansard_reports,
    search_contributions,
    search_debate_titles,
)
from ni_assembly_mcp.tools.member_detail import (
    get_detailed_member_information,
    get_registered_interests,
    get_state_of_the_parties,
    list_ministerial_roles,
)
from ni_assembly_mcp.tools.members import search_members
from ni_assembly_mcp.tools.plenary import (
    get_business_diary,
    get_divisions,
    get_motion_context,
    get_no_day_named_motions,
    search_plenary_business,
)
from ni_assembly_mcp.tools.questions import get_question_details, search_parliamentary_questions
from ni_assembly_mcp.tools.reference import (
    get_constituencies,
    get_departments,
    get_parties,
    list_all_committees,
    list_all_party_groups,
    list_organisations,
)

# Hard ceiling on any count-like argument (``max_results`` / ``num_contributors`` /
# ``num_contributions``), whatever the form or the tool default says. The public
# UI must not let a visitor ask for an unbounded hydrate.
HARD_RESULT_CAP = 100
_COUNT_ARGS = frozenset({"max_results", "num_contributors", "num_contributions"})


@dataclass(frozen=True)
class FormField:
    """One input on a form.

    ``kind`` drives both the HTML control and the string→value coercion:

    * ``text``   — ``<input type=text>``            → ``str``
    * ``date``   — ``<input type=date>``            → ``str`` (``YYYY-MM-DD``)
    * ``int``    — ``<input type=number>``          → ``int``
    * ``bool``   — ``<select>`` default/Yes/No      → ``bool`` (unset ⇒ tool default)
    * ``choice`` — ``<select>`` over ``choices``    → ``str``

    An empty submitted value is dropped, so the tool sees its own default.
    """

    name: str
    label: str
    kind: str = "text"
    choices: tuple[str, ...] = ()
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class FormSpec:
    name: str  # URL slug: /forms/<name>
    title: str
    summary: str
    tool: Callable[..., Awaitable[object]]
    group: str
    fields: tuple[FormField, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)  # sample questions this form answers
    provides: str = ""  # plain-English description of what you get back


def _date(name: str, label: str, help: str = "") -> FormField:
    return FormField(name, label, kind="date", help=help)


def _count(default_hint: int) -> FormField:
    return FormField(
        "max_results",
        "Max results",
        kind="int",
        help=f"1-{HARD_RESULT_CAP} (default {default_hint}).",
    )


REFERENCE = "Reference lists"
MEMBERS = "Members"
QUESTIONS = "Questions"
PLENARY = "Plenary business & divisions"
HANSARD = "Official Report (Hansard)"
COMMITTEES = "Committees"


FORMS: tuple[FormSpec, ...] = (
    # --- reference lists (no arguments) --------------------------------------
    FormSpec("departments", "Executive departments", "Current NICS departments.", get_departments, REFERENCE,
             examples=("Which department is 'DfE'?", "What are all the NI Civil Service departments?"),
             provides="A plain list of current departments with their names and ids, for use as filters elsewhere."),
    FormSpec("parties", "Political parties", "Parties currently represented.", get_parties, REFERENCE,
             examples=("What parties currently have seats in the Assembly?",),
             provides="A list of parties with their ids and names, for use as filters elsewhere."),
    FormSpec("party-groups", "All-Party Groups", "Current cross-party interest groups.",
             list_all_party_groups, REFERENCE,
             examples=("Is there an All-Party Group on cancer, or on the environment?",),
             provides="A list of current cross-party interest groups and what they cover."),
    FormSpec("organisations", "All organisations", "Every current organisation, combined.",
             list_organisations, REFERENCE,
             examples=("What's the full list of departments, parties and committees, in one place?",),
             provides="Every current organisation the Assembly recognises, combined into one list."),
    FormSpec(
        "committees",
        "Committees",
        "The Assembly's current committees.",
        list_all_committees,
        REFERENCE,
        (FormField("committee_type", "Category", kind="choice",
                   choices=("all", "standing", "statutory", "adhoc", "other")),),
        examples=("Which committee looks at health?", "What statutory committees exist right now?"),
        provides="A list of current committees (optionally filtered by type) with their ids and names.",
    ),
    FormSpec("constituencies", "Constituencies", "The 18 Assembly constituencies.", get_constituencies, REFERENCE,
             examples=("What are the 18 Assembly constituencies?",),
             provides="A list of the 18 constituencies with their ids, for use as filters elsewhere."),
    # --- members -----------------------------------------------------------
    FormSpec(
        "members",
        "Search members (MLAs)",
        "One filter dimension is used, in precedence order: date > name > constituency > party.",
        search_members,
        MEMBERS,
        (
            FormField("name", "Surname", help="Partial surname, min 3 chars. Current members only."),
            FormField("constituency_id", "Constituency id", kind="int", help="See the Constituencies list."),
            FormField("party_id", "Party id", kind="int", help="Party organisation id — see the Parties list."),
            _date("as_of_date", "As of date", "Membership as it stood on this date; includes historical members."),
            FormField("current_only", "Current members only", kind="bool",
                      help="Only applies when no other filter is set. Default Yes."),
            _count(25),
        ),
        examples=("Who is my local MLA?", "Which MLAs represent North Down?", "Who are the current DUP MLAs?"),
        provides="A list of matching MLAs — name, party, constituency and PersonId (needed for other forms).",
    ),
    FormSpec(
        "member-detail",
        "Member profile",
        "Core record plus optional roles, contact details and registered interests.",
        get_detailed_member_information,
        MEMBERS,
        (
            FormField("member_id", "Member PersonId", kind="int", required=True, help="From the member search."),
            FormField("include_roles", "Include roles", kind="bool", help="Default Yes."),
            FormField("include_contact", "Include contact details", kind="bool", help="Default No."),
            FormField("include_registered_interests", "Include registered interests", kind="bool", help="Default No."),
        ),
        examples=("What roles does this MLA hold?", "What's this MLA's constituency office contact info?"),
        provides="One MLA's full profile: roles, contact details and/or registered interests, as requested.",
    ),
    FormSpec(
        "registered-interests",
        "Register of Members' Interests",
        "The whole register, filterable by member or category.",
        get_registered_interests,
        MEMBERS,
        (
            FormField("member_id", "Member PersonId", kind="int"),
            FormField("category", "Category", help="Substring, e.g. 'donations'."),
            _count(100),
        ),
        examples=("Does my MLA have any relevant financial interests?", "Who has declared donations?"),
        provides="Register of Members' Interests entries, optionally narrowed to one member or category.",
    ),
    FormSpec(
        "ministerial-roles",
        "Ministerial roles",
        "Current ministerial roles and their holders (the present Executive).",
        list_ministerial_roles,
        MEMBERS,
        (FormField("include_junior_ministers", "Include junior Ministers", kind="bool", help="Default Yes."),),
        examples=("Who is the Minister for Health?", "Who holds each Executive department?"),
        provides="A list of ministerial roles and who currently holds each one.",
    ),
    FormSpec(
        "state-of-the-parties",
        "State of the parties",
        "Seat counts by party, now or on a given date.",
        get_state_of_the_parties,
        MEMBERS,
        (_date("as_of_date", "As of date", "Omit for the current Assembly."),),
        examples=("How many seats does each party currently hold?", "What was the party balance on a past date?"),
        provides="A seat count per party, for the current Assembly or as it stood on a given date.",
    ),
    # --- questions -------------------------------------------------------
    FormSpec(
        "questions",
        "Search parliamentary questions",
        "Written and oral questions. Filters combine freely with the local index; without it, "
        "exactly one server-side dimension is used.",
        search_parliamentary_questions,
        QUESTIONS,
        (
            FormField("query", "Keywords", help="Question (and, with the index, answer) text."),
            _date("date_from", "Tabled on/after"),
            _date("date_to", "Tabled on/before"),
            FormField("party", "Asking member's party", help="Name or abbreviation. Applied client-side."),
            FormField("asking_member_id", "Asking member PersonId", kind="int"),
            FormField("answering_body_name", "Answering department", help="e.g. 'Health' or 'DoF'."),
            _count(25),
        ),
        examples=("Has anyone asked the Minister about school transport this year?",
                   "What questions has this MLA asked?", "Which questions did Health answer last month?"),
        provides="A list of matching questions with asking member, department and date, plus a document id "
                 "to look up the full answer.",
    ),
    FormSpec(
        "question-detail",
        "Question detail",
        "One question's full record, including the answer text.",
        get_question_details,
        QUESTIONS,
        (FormField("document_id", "Document id", kind="int", required=True,
                   help="From the question search."),),
        examples=("What was the Minister's actual answer to this question?",),
        provides="The full text of one question and the Minister's answer, given its document id.",
    ),
    # --- plenary business & divisions -----------------------------------
    FormSpec(
        "plenary-business",
        "Search plenary business",
        "Tabled motions, amendments, ministerial statements and urgent oral questions.",
        search_plenary_business,
        PLENARY,
        (
            FormField("query", "Keywords", help="Literal substring on title and text."),
            _date("date_from", "From"),
            _date("date_to", "To"),
            FormField("date_basis", "Date basis", kind="choice", choices=("tabled", "scheduled")),
            FormField("member_id", "Tabling member PersonId", kind="int"),
            FormField("plenary_type", "Item type", help="Substring, e.g. 'Motion'."),
            FormField("include_tablers", "Include tablers", kind="bool", help="Default Yes."),
            _count(25),
        ),
        examples=("Is there a debate coming up about this?", "What motions has this MLA tabled?"),
        provides="A list of tabled motions, amendments, statements and urgent questions matching your filters.",
    ),
    FormSpec(
        "business-diary",
        "Business diary",
        "Sittings, committee meetings and events between two dates.",
        get_business_diary,
        PLENARY,
        (
            _date("start_date", "Start date", "Required."),
            _date("end_date", "End date", "Required."),
            FormField("event_type", "Event type", help="Substring, e.g. 'Committee Meeting'."),
            FormField("organisation", "Organisation", help="Substring, e.g. 'Committee for Health'."),
            _count(100),
        ),
        examples=("What's on a committee's agenda this week?", "When is the Assembly next sitting?"),
        provides="A list of sittings, committee meetings and other events between the two dates you give.",
    ),
    FormSpec(
        "divisions",
        "Divisions (recorded votes)",
        "A date-range list, or one division's result plus per-member voting.",
        get_divisions,
        PLENARY,
        (
            FormField("document_id", "Division document id", kind="int",
                      help="Given → that one division; date args ignored."),
            _date("date_from", "From"),
            _date("date_to", "To"),
            FormField("member_id", "Member PersonId", kind="int",
                      help="Keep only divisions this member voted in, plus their vote."),
            FormField("include_results", "Include results", kind="bool", help="Default Yes."),
            _count(25),
        ),
        examples=("How did my MLA vote on this bill?", "What divisions happened last month?"),
        provides="A list of recorded votes, or (given a document id) one division's full result and "
                 "how each member voted.",
    ),
    FormSpec(
        "motion-context",
        "Motion context",
        "A motion's details, tablers, amendments, linked Bill and any Petition of Concern.",
        get_motion_context,
        PLENARY,
        (FormField("document_id", "Motion document id", kind="int", required=True,
                   help="From the plenary-business search."),),
        examples=("Who tabled this motion, and were there any amendments?",
                   "Was a Petition of Concern raised against this motion?"),
        provides="One motion's full context: text, tablers, amendments, linked Bill and Petition of Concern status.",
    ),
    FormSpec(
        "no-day-named-motions",
        "No-day-named motions",
        "Motions tabled and signed but with no scheduled debate date.",
        get_no_day_named_motions,
        PLENARY,
        (
            FormField("query", "Keywords", help="Literal substring on title/text."),
            _count(100),
        ),
        examples=("Are there any motions on this topic still waiting for a debate date?",),
        provides="A list of signed motions that have no scheduled debate date yet.",
    ),
    # --- Hansard --------------------------------------------------------
    FormSpec(
        "hansard-reports",
        "Hansard sitting days",
        "Official Report sitting days, newest first (no local index needed).",
        get_hansard_reports,
        HANSARD,
        (_date("date_from", "From"), _date("date_to", "To"), _count(50)),
        examples=("What sitting days has the Assembly had recently?",),
        provides="A list of Official Report sitting days in the range, newest first.",
    ),
    FormSpec(
        "debate-titles",
        "Search debate titles",
        "Debate and section headings in a date range. Needs the local Hansard index.",
        search_debate_titles,
        HANSARD,
        (
            FormField("query", "Keywords", required=True, help="Stemmed, not semantic."),
            _date("date_from", "From", "Default: ~6 months ago."),
            _date("date_to", "To", "Default: today."),
            _count(25),
        ),
        examples=("Has there been a debate about this topic recently?", "What debates covered childcare?"),
        provides="A list of matching debate/section titles with their dates, to browse or feed into other forms.",
    ),
    FormSpec(
        "contributions",
        "Search contributions",
        "Full-text search over what members said in the Chamber. Needs the local Hansard index.",
        search_contributions,
        HANSARD,
        (
            FormField("query", "Keywords", help="Stemmed, BM25-ranked; not semantic."),
            FormField("member_id", "Member PersonId", kind="int"),
            _date("date_from", "From"),
            _date("date_to", "To"),
            _count(50),
        ),
        examples=("What did someone say in the Chamber about this?", "What has this MLA said about housing?"),
        provides="A list of individual spoken contributions matching your keywords, with speaker and date.",
    ),
    FormSpec(
        "relevant-contributors",
        "Find relevant contributors",
        "Members ranked by how much they spoke on the query terms. Needs the local Hansard index.",
        find_relevant_contributors,
        HANSARD,
        (
            FormField("query", "Topic keywords", required=True),
            FormField("num_contributors", "Max members", kind="int", help=f"1-{HARD_RESULT_CAP} (default 10)."),
            FormField("num_contributions", "Examples per member", kind="int",
                      help=f"1-{HARD_RESULT_CAP} (default 10)."),
            _date("date_from", "From"),
            _date("date_to", "To"),
        ),
        examples=("Which MLAs speak most often about this issue?", "Who are the go-to voices on this topic?"),
        provides="A ranked list of members by how much they've spoken on the topic, with example quotes.",
    ),
    # --- committees ----------------------------------------------------
    FormSpec(
        "committee-agenda",
        "Committee agenda",
        "Order of business for committee meetings, by meeting event id / committee+date / date.",
        get_committee_agenda,
        COMMITTEES,
        (
            _date("meeting_date", "Meeting date", "Required unless an event id is given."),
            FormField("committee_id", "Committee id", kind="int", help="Narrows a date lookup to one committee."),
            FormField("event_id", "Meeting event id", kind="int", help="Takes precedence over the date fields."),
        ),
        examples=("What's on a committee's agenda this week?", "What will this committee discuss at its next meeting?"),
        provides="The order of business for a committee meeting, found by meeting id, committee+date, or date alone.",
    ),
)

FORMS_BY_NAME: dict[str, FormSpec] = {spec.name: spec for spec in FORMS}

_ALLOWED = {fn.__name__ for fn in ALL_TOOLS}
_bad = sorted(spec.name for spec in FORMS if spec.tool.__name__ not in _ALLOWED)
if _bad:  # pragma: no cover - guard tripped only by a coding mistake
    raise RuntimeError(f"form specs target non-tool functions: {_bad}")


def clamp_counts(kwargs: dict[str, object]) -> dict[str, object]:
    """Clamp every count-like argument into ``1..HARD_RESULT_CAP`` in place."""
    for key in _COUNT_ARGS & kwargs.keys():
        value = kwargs[key]
        if isinstance(value, int):
            kwargs[key] = max(1, min(HARD_RESULT_CAP, value))
    return kwargs
