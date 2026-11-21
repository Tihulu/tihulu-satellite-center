// Removal routing for native scheduled jobs (web UI and native desktop share it).
//
// A record disappears from the armed-jobs list only when it also disappears from
// the native calendar, so every path ends in one scheduler refresh. A pending
// schedule is a live reservation: it is cancelled, never silently dropped.

/** @returns {{method:"cancel"|"clear", call:()=>Promise<any>}} */
export function dismissAction(job, bridge) {
  if (!bridge) throw new Error("native bridge unavailable");
  if (!job || typeof job.id !== "string" || !job.id) {
    throw new Error("a scheduled job is required");
  }
  if (job.state === "pending") {
    if (typeof bridge.nativeCancelScheduledJob !== "function") {
      throw new Error("native cancellation unavailable");
    }
    return {
      method: "cancel",
      call: () => bridge.nativeCancelScheduledJob(String(job.id)),
    };
  }
  if (typeof bridge.nativeClearScheduledJob !== "function") {
    throw new Error("native record removal unavailable");
  }
  return {
    method: "clear",
    call: () => bridge.nativeClearScheduledJob(String(job.id)),
  };
}

/** Finished (non-pending) records that a bulk clear would remove. */
export function finishedJobs(jobs) {
  return (jobs || []).filter((job) => job && job.state !== "pending");
}

/** Pending schedules that a bulk clear must never touch. */
export function pendingJobs(jobs) {
  return (jobs || []).filter((job) => job && job.state === "pending");
}
