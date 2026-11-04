# System status through IRI (optional)

IRI (the DOE Integrated Research Infrastructure) gives each facility the same REST API
for its systems. A resource agent (`ROLE=resources`, see
[campaigns.md](campaigns.md#resources)) has a read-only `system_status` tool wherever a
system has IRI settings, and a campaign can give it to its own agents. It returns for
one system:

- whether it is up, and any open incidents (outages, maintenance)
- all your current batch jobs there (this run's blocks and any others of yours), with
  their state: queued, held or active
- how many jobs are queued, held and running in each queue the campaign's resource
  buckets use, and in each queue your jobs are in

These are counts only. The API does not give a job's place in the queue or when it is
expected to start.

Submitting and cancelling stay with the framework, so budgets and resource buckets apply
to every job.

## Turning it on

For agents other than a resource agent, in `campaign.json`:

```json
"iri": true
```

Either way, every system the campaign uses that has an `iri` block in
`systems/<system>.json` is covered. Polaris has one:

```json
"iri": {
  "base_url": "https://api.alcf.anl.gov/api/v2",
  "compute_resource": "55c1c993-1124-47f9-b823-514ba3849a9a",
  "token_command": ["alcf-tokens", "get-token", "iri"]
}
```

`compute_resource` is the system's id in `GET <base_url>/status/resources`, which needs
no token. `token_command` prints an access token. The same block in
`users/<you>/<system>.json` overrides any of these for you, and may set `username` (your
login at the facility, when it is not your name in `users/`).

## Token (ALCF)

In the environment the lab runs in (the agents and the watch both call it):

```
pip install alcf-tokens
alcf-tokens login iri
```

The login opens a URL to sign in with your ALCF credentials. `alcf-tokens get-token iri`
then refreshes the token as needed, for up to 7 days, after which you log in again. See
the [ALCF IRI API guide](https://docs.alcf.anl.gov/services/iri-api/).

Preflight checks the token. A login that has lapsed fails it, with the facility's reason.
