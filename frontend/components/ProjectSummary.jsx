"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);

// Displays a summary of a selected project, including tasks and next steps.
export default function ProjectSummary({ projectId, onAskIV }) {
  const [project, setProject] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [updating, setUpdating] = useState(false);
  const [showUpdatePrompt, setShowUpdatePrompt] = useState(false);
  const [targetTaskId, setTargetTaskId] = useState(null);

  const fetchProjectDetails = useCallback(async () => {
    try {
      setLoading(true);
      const projectRes = await fetchWithTimeout(api(`/projects/${projectId}`), { cache: "no-store" }, 15000);
      if (!projectRes.ok) throw new Error("Failed to fetch project");
      setProject(await projectRes.json());

      const tasksRes = await fetchWithTimeout(
        api(`/tasks?project_id=${encodeURIComponent(projectId)}`),
        { cache: "no-store" },
        15000,
      );
      if (!tasksRes.ok) throw new Error("Failed to fetch tasks");
      setTasks(await tasksRes.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchProjectDetails();
  }, [fetchProjectDetails]);

  if (loading) return <p className="sidebar-muted">Loading project summary…</p>;
  if (error) return <p className="sidebar-error">Error: {error}</p>;
  if (!project) return <p className="sidebar-muted">No project selected.</p>;

  // Derive next step: first incomplete task, or "All tasks complete!"
  const nextTask = tasks.find((task) => task.status !== "completed");
  const nextStep = nextTask ? `Continue with: "${nextTask.title}"` : "All tasks complete.";

  const handleContinue = (taskId) => {
    setTargetTaskId(taskId);
    setShowUpdatePrompt(true);
  };

  const confirmUpdate = async (newStatus) => {
    setUpdating(true);
    try {
      const res = await fetchWithTimeout(
        api(`/tasks/${encodeURIComponent(targetTaskId)}/status`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: newStatus }),
        },
        15000,
      );
      if (!res.ok) throw new Error(`Failed to update task (HTTP ${res.status})`);
      const updated = await res.json();
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setShowUpdatePrompt(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setUpdating(false);
    }
  };

  const askIV = () => {
    if (!nextTask) return;
    onAskIV?.(
      `Plan the next steps for task "${nextTask.title}" on the "${project.name}" project. ` +
        "Suggest a concise plan with clear next actions.",
    );
  };

  return (
    <div className="project-summary">
      <h4>{project.name}</h4>
      {project.description && <p>{project.description}</p>}
      <p className="project-summary-status"><strong>Status:</strong> {project.status}</p>

      <div className="milestones">
        <h5>Tasks</h5>
        {tasks.length === 0 ? (
          <p className="sidebar-muted">No tasks yet.</p>
        ) : (
          <ul>
            {tasks.map((task) => (
              <li key={task.id} className={task.status === "completed" ? "completed" : ""}>
                <span className="task-name">{task.title}</span>
                <span className="task-status">{task.status}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="next-step">
        <p><strong>Next step:</strong> {nextStep}</p>
        {nextTask && (
          <div className="next-step-actions">
            <button className="continue-button" onClick={() => handleContinue(nextTask.id)}>
              Continue &rarr;
            </button>
            <button className="ask-iv-button" onClick={askIV}>
              Ask iV &rarr;
            </button>
          </div>
        )}
      </div>

      {showUpdatePrompt && (
        <div className="modal-overlay" onClick={() => !updating && setShowUpdatePrompt(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p>Update this task to:</p>
            <div className="modal-actions">
              <button disabled={updating} onClick={() => confirmUpdate("in_progress")}>In progress</button>
              <button disabled={updating} onClick={() => confirmUpdate("completed")}>Completed</button>
              <button disabled={updating} onClick={() => setShowUpdatePrompt(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
