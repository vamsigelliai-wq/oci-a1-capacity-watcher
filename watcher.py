"""Check A1 capacity and apply one previously reviewed OCI Resource Manager stack."""
import hashlib
import io
import os
import sys
import uuid
import zipfile

import oci
from control import expired

SHAPE = "VM.Standard.A1.Flex"
FAULT_DOMAINS = {f"FAULT-DOMAIN-{i}" for i in (1, 2, 3)}
ACTIVE_JOBS = {"ACCEPTED", "IN_PROGRESS", "CANCELING"}


class WatcherError(RuntimeError):
    """An intentionally public, fixed status message."""


def require(condition, message):
    if not condition:
        raise WatcherError(message)


def required(name):
    value = os.environ.get(name, "").strip()
    require(bool(value), f"Missing required setting: {name}")
    return value


def all_items(method, *args, **kwargs):
    return oci.pagination.list_call_get_all_results(method, *args, **kwargs).data


def result(message, complete=False):
    # Only fixed messages are written; never log IDs, IPs, keys, or OCI response bodies.
    print(message)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"complete={str(complete).lower()}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(message + "\n")


def verify_archive(raw, expected_hash):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [i for i in archive.infolist() if not i.is_dir()]
        require(len(files) == 1 and files[0].filename == "main.tf",
                "Stack archive differs from the reviewed single-file configuration.")
        require(files[0].file_size < 1_000_000, "Unexpected stack file size.")
        content = archive.read(files[0])
    require(hashlib.sha256(content).hexdigest() == expected_hash,
            "Stack configuration changed. Review it before enabling applies.")


def available(rows):
    require(len(rows) == 3 and {r.fault_domain for r in rows} == FAULT_DOMAINS,
            "Capacity response did not cover all three fault domains.")
    for row in rows:
        cfg = row.instance_shape_config
        require(row.instance_shape == SHAPE and cfg is not None
                and cfg.ocpus == 2 and cfg.memory_in_gbs == 12,
                "Capacity response did not match the requested 2 OCPU / 12 GB shape.")
        require(row.availability_status in {
            "AVAILABLE", "OUT_OF_HOST_CAPACITY", "HARDWARE_NOT_SUPPORTED"},
            "Unrecognized capacity status; no apply submitted.")
    require(any(r.availability_status != "HARDWARE_NOT_SUPPORTED" for r in rows),
            "The capacity API reports this shape as unsupported. No apply submitted.")
    return any(r.availability_status == "AVAILABLE" and
               (r.available_count is None or r.available_count >= 1) for r in rows)


def capacity_failure(log_text):
    """Retry only a recognizable capacity error with no other Terraform errors."""
    import re
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(log_text))
    # Resource Manager prefixes every Terraform line with its timestamp and level.
    text = re.sub(r"(?m)^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}"
                  r"\[TERRAFORM_CONSOLE\] \[[A-Z]+\] ?", "", text)
    errors = re.findall(r"(?:^|\n)[^\S\n]*[│|]?\s*Error:\s*([^\n]+)", text)
    markers = ("out of host capacity", "out of capacity", "outofhostcapacity",
               "out_of_host_capacity")
    return bool(errors) and all(any(m in e.lower() for m in markers) for e in errors)


def check_jobs(rm, stack_id):
    jobs = all_items(rm.list_jobs, stack_id=stack_id,
                     sort_by="TIMECREATED", sort_order="DESC")
    if any(j.lifecycle_state in ACTIVE_JOBS for j in jobs):
        return "busy"
    applies = [j for j in jobs if j.operation == "APPLY"]
    # A historical successful apply permanently blocks further automatic applies.
    if any(j.lifecycle_state == "SUCCEEDED" for j in applies):
        return "succeeded"
    if applies:
        latest = applies[0]
        require(latest.lifecycle_state == "FAILED",
                "The last apply was canceled or has an unexpected state. Review it in OCI.")
        logs = rm.get_job_logs_content(latest.id).data
        require(capacity_failure(logs),
                "The last apply has an error other than recognizable capacity exhaustion. Review it in OCI.")
    return "ready"


def inventory(identity, compute, block, tenancy, ads):
    compartments = [tenancy] + [c.id for c in all_items(
        identity.list_compartments, tenancy, compartment_id_in_subtree=True,
        access_level="ANY", lifecycle_state="ACTIVE")]
    instances, storage = [], 0
    # Fail rather than assume an inaccessible compartment is empty.
    for compartment in compartments:
        instances.extend(i for i in all_items(compute.list_instances, compartment)
                         if i.lifecycle_state != "TERMINATED")
        for volume in all_items(block.list_volumes, compartment_id=compartment):
            if volume.lifecycle_state != "TERMINATED":
                storage += volume.size_in_gbs
        for ad in ads:
            for volume in all_items(block.list_boot_volumes,
                                    availability_domain=ad.name, compartment_id=compartment):
                if volume.lifecycle_state != "TERMINATED":
                    storage += volume.size_in_gbs
    return instances, storage


def run():
    apply = "--apply" in sys.argv[1:]
    if apply:
        require(not expired(required("OCI_STOP_AT")),
                "Provisioning window ended. No apply allowed.")
    tenancy, stack_id = required("OCI_TENANCY_ID"), required("OCI_STACK_ID")
    region, ad = required("OCI_REGION"), required("OCI_AVAILABILITY_DOMAIN")
    require(region == "ap-hyderabad-1", "This reviewed setup targets Hyderabad only.")
    config = {"tenancy": tenancy, "user": required("OCI_USER_ID"),
              "fingerprint": required("OCI_FINGERPRINT"), "region": region,
              "key_content": required("OCI_API_KEY")}
    if os.environ.get("OCI_KEY_PASSPHRASE"):
        config["pass_phrase"] = os.environ["OCI_KEY_PASSPHRASE"]
    oci.config.validate_config(config)
    # One attempt per API call. Throttling or other errors pause the workflow for review.
    opts = {"retry_strategy": oci.retry.NoneRetryStrategy(), "timeout": (10, 30)}
    identity = oci.identity.IdentityClient(config, **opts)
    compute = oci.core.ComputeClient(config, **opts)
    block = oci.core.BlockstorageClient(config, **opts)
    rm = oci.resource_manager.ResourceManagerClient(config, **opts)
    subscriptions = all_items(identity.list_region_subscriptions, tenancy)
    require(any(s.is_home_region and s.region_name == region for s in subscriptions),
            "Hyderabad must be the tenancy home region for this free-tier setup.")
    ads = all_items(identity.list_availability_domains, tenancy)
    require(any(a.name == ad for a in ads), "Configured availability domain was not found.")
    stack_response = rm.get_stack(stack_id)
    stack = stack_response.data
    require(stack.lifecycle_state == "ACTIVE" and stack.compartment_id == tenancy,
            "Expected an active stack in the root compartment.")
    require(stack.config_source.config_source_type == "ZIP_UPLOAD",
            "Only the reviewed uploaded ZIP configuration is supported.")
    data = rm.get_stack_tf_config(stack_id).data
    verify_archive(data.content, required("OCI_MAIN_TF_SHA256"))
    jobs_state = check_jobs(rm, stack_id)
    if jobs_state == "busy":
        return result("An OCI job is already active. Checking again on the next schedule.")
    instances, storage = inventory(identity, compute, block, tenancy, ads)
    if jobs_state == "succeeded":
        targets = [i for i in instances if i.display_name == required("OCI_INSTANCE_NAME")
                   and i.shape == SHAPE]
        require(len(targets) == 1 and targets[0].lifecycle_state == "RUNNING"
                and targets[0].shape_config.ocpus == 2
                and targets[0].shape_config.memory_in_gbs == 12,
                "The stack has already succeeded. Instance needs review; no further apply allowed.")
        return result("The stack succeeded and the 2 OCPU / 12 GB instance is RUNNING. Automatic retries are finished.", True)
    require(not any(i.shape == SHAPE for i in instances),
            "Existing A1 usage detected. No additional instance will be requested.")
    require(storage + 100 <= 200,
            "Adding the reviewed 100 GB boot disk could exceed the 200 GB free storage allowance.")
    report = compute.create_compute_capacity_report(
        oci.core.models.CreateComputeCapacityReportDetails(
            compartment_id=tenancy, availability_domain=ad,
            shape_availabilities=[oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                fault_domain=fd, instance_shape=SHAPE,
                instance_shape_config=oci.core.models.CapacityReportInstanceShapeConfig(
                    ocpus=2, memory_in_gbs=12)) for fd in sorted(FAULT_DOMAINS)])).data
    if not available(report.shape_availabilities):
        return result("No matching capacity in the three fault domains. Checking again on the next schedule.")
    if not apply:
        return result("Matching capacity was reported. Check-only run: no stack apply submitted.")
    # Recheck immediately before submitting. Do not manually apply/edit the stack while enabled.
    if check_jobs(rm, stack_id) != "ready":
        return result("Stack job state changed. No new apply submitted.")
    current_stack = rm.get_stack(stack_id)
    require(stack_response.headers.get("etag") is not None and
            current_stack.headers.get("etag") == stack_response.headers.get("etag"),
            "Stack changed during the check. No apply submitted.")
    require(not expired(required("OCI_STOP_AT")),
            "Provisioning window ended during checks. No apply submitted.")
    job = rm.create_job(oci.resource_manager.models.CreateJobDetails(
        stack_id=stack_id, display_name="a1-capacity-watcher",
        operation="APPLY", job_operation_details=
        oci.resource_manager.models.CreateApplyJobOperationDetails(
            execution_plan_strategy="AUTO_APPROVED")),
        opc_retry_token=str(uuid.uuid4())).data
    # Return quickly. OCI executes the job even after this GitHub runner exits.
    result("Matching capacity was reported and one OCI stack apply was submitted. The next run will check its result.")


if __name__ == "__main__":
    try:
        run()
    except WatcherError as exc:
        # Only our messages contain fixed text and setting NAMES.
        print(f"::error::{exc}")
        sys.exit(1)
    except oci.exceptions.ServiceError as exc:
        # OCI exceptions can contain private IDs and URLs: never print the object.
        print(f"::error::OCI request failed (HTTP {int(exc.status)}). Check credentials, IAM permissions, and the OCI console. No fallback apply attempted.")
        sys.exit(1)
    except Exception:
        print("::error::Watcher could not finish its checks. No raw response or traceback is logged. Review configuration privately.")
        sys.exit(1)
