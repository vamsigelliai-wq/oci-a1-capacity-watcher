# OCI A1 capacity watcher

Check for one **VM.Standard.A1.Flex instance with 2 OCPUs and 12 GB RAM** in Hyderabad, then apply an existing, reviewed OCI Resource Manager stack. Terraform state stays in OCI.

**Status: credentials required; scheduled cloud changes are gated off until OCI_ENABLED is set to true.**

## Behavior

- Requests matching capacity in FAULT-DOMAIN-1, FAULT-DOMAIN-2, and FAULT-DOMAIN-3.
- Checks every ten minutes when enabled. GitHub schedules can be delayed or skipped.
- Submits one apply only when OCI reports capacity; an existing active job prevents another apply.
- Checks existing A1 usage and regional boot/block storage before adding the reviewed 100 GB boot disk.
- Rejects changes to the reviewed single-file Terraform configuration using its SHA256.
- Retries recognizable capacity errors; other errors require review in the private OCI console.
- Stops further applies after a successful apply; disables this workflow after verifying the expected instance is RUNNING.
- Prints fixed status messages. Never prints raw OCI responses, account identifiers, IP addresses, configuration, or keys.

## Credentials and private configuration

Add these as **repository Secrets** under Settings → Secrets and variables → Actions → Secrets:

| Secret name | Purpose |
|---|---|
| OCI_TENANCY_ID | Tenancy OCID |
| OCI_USER_ID | API signing user's OCID |
| OCI_FINGERPRINT | API signing key fingerprint |
| OCI_API_KEY | Complete PEM API signing private key, not the server SSH key |
| OCI_KEY_PASSPHRASE | Optional API key passphrase |
| OCI_STACK_ID | Existing Resource Manager stack OCID |
| OCI_AVAILABILITY_DOMAIN | Full tenancy-specific availability-domain name |
| OCI_MAIN_TF_SHA256 | SHA256 of the exact reviewed main.tf bytes |
| OCI_INSTANCE_NAME | Instance display name in the reviewed stack |

Actual values must never go in source files, repository Variables, workflow inputs, commits, artifacts, or comments. Do not upload the stack ZIP or server SSH key. This public repository's code and run history are visible to others. Stored Secrets are not displayed to visitors; anyone trusted with repository write access can modify workflows that use them.

Use a dedicated OCI automation identity with the necessary read, capacity-report, Resource Manager apply, and underlying compute/network permissions. Do not grant tenancy administrator privileges merely to fix a permission error. The existing stack must be reviewed before allowing unattended applies.

## Enable

1. Add the Secrets above after configuring the OCI API signing identity.
2. Open Actions → OCI A1 capacity watcher → Run workflow and choose **check**. This mode never submits an apply. Resolve any errors privately in OCI.
3. Only after a successful live check, create the non-sensitive repository **Variable** OCI_ENABLED with value true. Scheduled runs can then submit an apply without another prompt.
4. The schedule requests runs at UTC minutes 07, 17, 27, 37, 47, and 57.

Set OCI_ENABLED to false or disable the workflow to pause. Already-submitted OCI jobs continue independently. Do not manually apply or edit the stack while the watcher is enabled. Remove its OCI API key and GitHub secret after the automation is no longer needed.

## Limits

A capacity report is a snapshot, not a reservation. Capacity may disappear before the stack launches. Unsupported or unauthorized capacity reports do not trigger a blind-apply fallback. There is no guaranteed launch date.

Standard GitHub-hosted runners in public repositories are free under current GitHub billing rules. Public scheduled workflows may be disabled after 60 days without repository activity. Always Free eligibility depends on home region and aggregate account usage; inventory checks are not billing caps. This workflow does not upgrade the account or buy a reservation.

Oracle may reclaim idle Always Free instances. A single free VM does not guarantee uninterrupted service, and capacity for 10–15 concurrent application workflows needs a workload test. This repository provisions infrastructure; it does not install an API or agent application.

## References

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle capacity reports](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/models/oci.core.models.CreateCapacityReportShapeAvailabilityDetails.html)
- [Oracle Resource Manager permissions](https://docs.oracle.com/en-us/iaas/Content/Security/Reference/resourcemanager_security.htm)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
