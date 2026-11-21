import test from "node:test";
import assert from "node:assert/strict";

const {dismissAction, finishedJobs, pendingJobs} = await import("./job_removal.mjs");

test("dismissing a pending schedule cancels it instead of dropping the record", () => {
  const calls = [];
  const bridge = {
    nativeCancelScheduledJob: (id) => { calls.push(["cancel", id]); return Promise.resolve({id}); },
    nativeClearScheduledJob: (id) => { calls.push(["clear", id]); return Promise.resolve({id}); },
  };
  const action = dismissAction({id: "job-1", state: "pending"}, bridge);
  assert.equal(action.method, "cancel");
  return action.call().then(() => assert.deepEqual(calls, [["cancel", "job-1"]]));
});

test("dismissing a finished record clears it", () => {
  const calls = [];
  const bridge = {
    nativeCancelScheduledJob: (id) => { calls.push(["cancel", id]); return Promise.resolve({id}); },
    nativeClearScheduledJob: (id) => { calls.push(["clear", id]); return Promise.resolve({id}); },
  };
  for (const state of ["cancelled", "completed", "blocked"]) {
    const action = dismissAction({id: "job-2", state}, bridge);
    assert.equal(action.method, "clear", state);
  }
  return dismissAction({id: "job-2", state: "completed"}, bridge).call()
    .then(() => assert.deepEqual(calls, [["clear", "job-2"]]));
});

test("a missing bridge is reported, never silently ignored", () => {
  assert.throws(() => dismissAction({id: "j", state: "cancelled"}, null), /bridge/);
  const partial = {nativeClearScheduledJob: () => Promise.resolve()};
  assert.throws(() => dismissAction({id: "j", state: "pending"}, partial), /cancellation/);
});

test("an invalid job is refused", () => {
  const bridge = {nativeCancelScheduledJob: () => Promise.resolve(), nativeClearScheduledJob: () => Promise.resolve()};
  assert.throws(() => dismissAction(null, bridge), /scheduled job/);
  assert.throws(() => dismissAction({state: "cancelled"}, bridge), /scheduled job/);
});

test("bulk clear separates finished records from live reservations", () => {
  const jobs = [
    {id: "a", state: "pending"},
    {id: "b", state: "completed"},
    {id: "c", state: "cancelled"},
    {id: "d", state: "pending"},
    {id: "e", state: "blocked"},
  ];
  assert.deepEqual(finishedJobs(jobs).map((j) => j.id), ["b", "c", "e"]);
  assert.deepEqual(pendingJobs(jobs).map((j) => j.id), ["a", "d"]);
});
