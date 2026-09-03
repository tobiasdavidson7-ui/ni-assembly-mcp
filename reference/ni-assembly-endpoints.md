# NI Assembly Open Data API — source endpoint list

> **Placeholder.** The endpoint list referenced in the original planning prompt was not
> captured before the chat message was lost. Paste it here verbatim.
>
> Until then, the working inventory lives in [`../PLAN.md`](../PLAN.md) **Appendix A**, which was
> reconstructed from the live `.asmx` service-description pages on 2026-09-03 and the
> [paddycarey gist](https://gist.github.com/paddycarey/3752817).
>
> Once this file holds the original list, diff it against Appendix A and reconcile any
> operation that appears in only one (see PLAN.md §5a).

## Services

- `https://data.niassembly.gov.uk/members.asmx`
- `https://data.niassembly.gov.uk/organisations.asmx`
- `https://data.niassembly.gov.uk/plenary.asmx`
- `https://data.niassembly.gov.uk/hansard.asmx`
- `https://data.niassembly.gov.uk/register.asmx`
- `https://data.niassembly.gov.uk/questions.asmx`

## URL pattern

```
https://data.niassembly.gov.uk/<service>.asmx/<Operation>_JSON?<param>=<value>
```

WSDL / per-operation help (authoritative for names + param casing):

```
https://data.niassembly.gov.uk/<service>.asmx?op=<Operation>
```
