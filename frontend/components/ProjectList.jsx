"use client";

// Renders a list of projects with selection.
export default function ProjectList({ projects, selectedProjectId, onSelectProject }) {
  return (
    <ul className="project-list">
      {projects.map((project) => (
        <li key={project.id}>
          <button
            type="button"
            className={`project-item${selectedProjectId === project.id ? " selected" : ""}`}
            onClick={() => onSelectProject(project.id)}
          >
            <span className="project-name">{project.name}</span>
            <span className="project-status">{project.status}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
