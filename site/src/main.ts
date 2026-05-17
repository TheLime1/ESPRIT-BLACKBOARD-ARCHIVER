import "./styles.css";

type ClassesIndex = {
  generatedAt?: string;
  classes?: ClassEntry[];
};

type ClassEntry = {
  family: string;
  latestClassCode?: string;
  classCodes?: string[];
  modes?: string[];
  latestRunAt?: string;
  courseCount?: number;
  htmlPages?: number;
  images?: number;
  attachments?: number;
  errors?: number;
  url: string;
};

type ClassManifest = {
  generatedAt: string;
  classCode: string;
  family: string;
  mode: string;
  stats: {
    courses: number;
    contents: number;
    html_pages: number;
    images: number;
    attachments: number;
    errors: number;
  };
  courses: CourseManifest[];
};

type CourseManifest = {
  id: string;
  courseId: string;
  name: string;
  classCode: string;
  url?: string;
  pages?: ArchivePage[];
};

type ArchivePage = {
  title: string;
  titlePath: string[];
  path: string;
};

type LoadedClass = {
  entry: ClassEntry;
  manifest: ClassManifest;
};

type PageItem = {
  page: ArchivePage;
  index: number;
  label: string;
};

type ChapterNode = {
  title: string;
  children: Map<string, ChapterNode>;
  pages: PageItem[];
};

type FlatPageTarget = {
  courseIndex: number;
  pageIndex: number;
  course: CourseManifest;
  page: ArchivePage;
};

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("Missing app root");
}

let loadedClasses: LoadedClass[] = [];
let activeClass = 0;
let activeCourse = 0;
let activePage = 0;
let query = "";

function formatDate(value?: string): string {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function pageCount(course: CourseManifest): number {
  return course.pages?.length ?? 0;
}

function allPages(manifest: ClassManifest): ArchivePage[] {
  return manifest.courses.flatMap((course) => course.pages ?? []);
}

function matchesCourse(course: CourseManifest, search: string): boolean {
  if (!search) return true;
  const haystack = [
    course.name,
    course.courseId,
    ...(course.pages ?? []).map((page) => `${page.title} ${page.titlePath.join(" ")}`),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(search);
}

function filteredCourses(manifest: ClassManifest): CourseManifest[] {
  const search = query.trim().toLowerCase();
  return manifest.courses.filter((course) => matchesCourse(course, search));
}

function activeLoadedClass(): LoadedClass | undefined {
  return loadedClasses[activeClass];
}

function activeFilteredCourses(): CourseManifest[] {
  const current = activeLoadedClass();
  return current ? filteredCourses(current.manifest) : [];
}

function setActivePage(courseIndex: number, pageIndex: number): void {
  activeCourse = courseIndex;
  activePage = pageIndex;
  render();
}

function flattenCoursePages(courses: CourseManifest[]): FlatPageTarget[] {
  return courses.flatMap((course, courseIndex) =>
    (course.pages ?? []).map((page, pageIndex) => ({
      courseIndex,
      pageIndex,
      course,
      page,
    })),
  );
}

function currentFlatIndex(targets: FlatPageTarget[]): number {
  return targets.findIndex((target) => target.courseIndex === activeCourse && target.pageIndex === activePage);
}

function renderReaderButton(label: string, target: FlatPageTarget | undefined, direction: "prev" | "next"): string {
  if (!target) {
    return `<span class="reader-nav reader-nav-disabled ${direction}">${label}</span>`;
  }
  return `
    <button class="reader-nav ${direction}" data-nav-course="${target.courseIndex}" data-nav-page="${target.pageIndex}">
      <span>${label}</span>
      <strong>${escapeHtml(target.page.title)}</strong>
      <small>${escapeHtml(target.course.name)}</small>
    </button>
  `;
}

function compactPath(path: string[]): string[] {
  const compacted: string[] = [];
  for (const segment of path.map((item) => item.trim()).filter(Boolean)) {
    if (compacted[compacted.length - 1] !== segment) {
      compacted.push(segment);
    }
  }
  return compacted;
}

function buildChapterTree(pages: ArchivePage[]): ChapterNode {
  const root: ChapterNode = { title: "Course", children: new Map(), pages: [] };

  pages.forEach((page, index) => {
    const path = compactPath(page.titlePath.length ? page.titlePath : [page.title]);
    const label = path[path.length - 1] || page.title || "Untitled";
    const groups = path.slice(0, -1);
    let current = root;

    for (const group of groups) {
      if (!current.children.has(group)) {
        current.children.set(group, { title: group, children: new Map(), pages: [] });
      }
      current = current.children.get(group)!;
    }

    current.pages.push({ page, index, label });
  });

  return root;
}

function countNodePages(node: ChapterNode): number {
  let total = node.pages.length;
  for (const child of node.children.values()) {
    total += countNodePages(child);
  }
  return total;
}

function renderPageButton(item: PageItem): string {
  return `
    <button class="page-row ${item.index === activePage ? "active" : ""}" data-page-index="${item.index}">
      <span class="page-icon" aria-hidden="true"></span>
      <span>${escapeHtml(item.label)}</span>
      <small>${escapeHtml(item.page.titlePath.join(" / "))}</small>
    </button>
  `;
}

function renderChapterNode(node: ChapterNode, depth = 0): string {
  const childNodes = [...node.children.values()];
  const pageButtons = node.pages.map(renderPageButton).join("");

  if (depth === 0) {
    return `
      <div class="chapter-tree">
        ${pageButtons ? `<div class="chapter-pages">${pageButtons}</div>` : ""}
        ${childNodes.map((child) => renderModuleCard(child)).join("")}
      </div>
    `;
  }

  return `
    <details class="section-group" open style="--depth: ${depth}">
      <summary>
        <span class="chapter-title">${escapeHtml(node.title)}</span>
        <span class="chapter-count">${countNodePages(node)}</span>
      </summary>
      <div class="chapter-children">
        ${pageButtons ? `<div class="chapter-pages">${pageButtons}</div>` : ""}
        ${childNodes.map((child) => renderChapterNode(child, depth + 1)).join("")}
      </div>
    </details>
  `;
}

function renderModuleCard(node: ChapterNode): string {
  const childNodes = [...node.children.values()];
  const pageButtons = node.pages.map(renderPageButton).join("");

  return `
    <details class="module-card" open>
      <summary class="module-summary">
        <span class="module-icon" aria-hidden="true"></span>
        <span class="module-copy">
          <strong>${escapeHtml(node.title)}</strong>
          <small>${countNodePages(node)} archived pages</small>
        </span>
        <span class="module-chevron" aria-hidden="true"></span>
      </summary>
      <div class="module-body">
        ${pageButtons ? `<div class="module-pages">${pageButtons}</div>` : ""}
        ${childNodes.map((child) => renderChapterNode(child, 2)).join("")}
      </div>
    </details>
  `;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char] ?? char;
  });
}

function renderEmpty(message: string): void {
  app.innerHTML = `
    <main class="empty">
      <h1>ESPRIT Blackboard Archive</h1>
      <p>${escapeHtml(message)}</p>
    </main>
  `;
}

function render(focusSearch = false): void {
  const current = activeLoadedClass();
  if (!current) {
    renderEmpty("No archive data found.");
    return;
  }

  const { manifest } = current;
  const courses = activeFilteredCourses();
  if (activeCourse >= courses.length) activeCourse = 0;
  const course = courses[activeCourse];
  const pages = course?.pages ?? [];
  if (activePage >= pages.length) activePage = 0;
  const page = pages[activePage];
  const chapterTree = buildChapterTree(pages);
  const flatPages = flattenCoursePages(courses);
  const flatIndex = currentFlatIndex(flatPages);
  const previousTarget = flatIndex > 0 ? flatPages[flatIndex - 1] : undefined;
  const nextTarget = flatIndex >= 0 && flatIndex + 1 < flatPages.length ? flatPages[flatIndex + 1] : undefined;
  const progressText = flatIndex >= 0 ? `Page ${flatIndex + 1} of ${flatPages.length}` : "No page selected";

  app.innerHTML = `
    <div class="shell">
      <header class="topbar">
        <div>
          <h1>Blackboard Archive</h1>
          <p>${escapeHtml(manifest.classCode)} · ${formatDate(manifest.generatedAt)}</p>
        </div>
        <label class="class-picker">
          <span>Class</span>
          <select id="classSelect">
            ${loadedClasses
              .map(
                (item, index) =>
                  `<option value="${index}" ${index === activeClass ? "selected" : ""}>${escapeHtml(
                    item.manifest.classCode,
                  )}</option>`,
              )
              .join("")}
          </select>
        </label>
      </header>

      <section class="workspace">
        <aside class="sidebar">
          <div class="search">
            <label for="searchInput">Search</label>
            <input id="searchInput" type="search" value="${escapeHtml(query)}" placeholder="Course, code, page" />
          </div>
          <div class="course-list" id="courseList">
            ${courses
              .map(
                (item, index) => `
                  <button class="course-row ${index === activeCourse ? "active" : ""}" data-course-index="${index}">
                    <span>${escapeHtml(item.name)}</span>
                    <small>${escapeHtml(item.courseId)} · ${pageCount(item)} pages</small>
                  </button>
                `,
              )
              .join("")}
          </div>
        </aside>

        <main class="content">
          ${
            course
              ? `
                <div class="reader-toolbar">
                  ${renderReaderButton("Prev", previousTarget, "prev")}
                  <div class="reader-title">
                    <span>${escapeHtml(progressText)}</span>
                    <h2>${escapeHtml(page?.title || course.name)}</h2>
                    <p>${escapeHtml(page?.titlePath.join(" / ") || course.courseId)}</p>
                  </div>
                  ${renderReaderButton("Next", nextTarget, "next")}
                  ${
                    course.url
                      ? `<a class="bb-link" href="${escapeHtml(course.url)}" target="_blank" rel="noreferrer">Blackboard</a>`
                      : ""
                  }
                </div>
                <div class="page-layout">
                  <nav class="page-list" aria-label="Archived pages">
                    <div class="page-nav-head">
                      <span>Modules</span>
                      <strong>${pages.length}</strong>
                    </div>
                    ${renderChapterNode(chapterTree)}
                  </nav>
                  <section class="preview">
                    ${
                      page
                        ? `<iframe title="${escapeHtml(page.title)}" src="${escapeHtml(page.path)}"></iframe>`
                        : `<div class="no-page">No HTML pages archived for this course.</div>`
                    }
                  </section>
                </div>
              `
              : `<div class="no-page">No courses match this search.</div>`
          }
        </main>
      </section>
    </div>
  `;

  bindEvents();

  if (focusSearch) {
    const searchInput = document.querySelector<HTMLInputElement>("#searchInput");
    searchInput?.focus();
    searchInput?.setSelectionRange(searchInput.value.length, searchInput.value.length);
  }
}

function bindEvents(): void {
  document.querySelector<HTMLSelectElement>("#classSelect")?.addEventListener("change", (event) => {
    activeClass = Number((event.currentTarget as HTMLSelectElement).value);
    activeCourse = 0;
    activePage = 0;
    render();
  });

  document.querySelector<HTMLInputElement>("#searchInput")?.addEventListener("input", (event) => {
    query = (event.currentTarget as HTMLInputElement).value;
    activeCourse = 0;
    activePage = 0;
    render(true);
  });

  document.querySelectorAll<HTMLButtonElement>("[data-course-index]").forEach((button) => {
    button.addEventListener("click", () => {
      activeCourse = Number(button.dataset.courseIndex);
      activePage = 0;
      render();
    });
  });

  document.querySelectorAll<HTMLButtonElement>("[data-page-index]").forEach((button) => {
    button.addEventListener("click", () => setActivePage(activeCourse, Number(button.dataset.pageIndex)));
  });

  document.querySelectorAll<HTMLButtonElement>("[data-nav-course][data-nav-page]").forEach((button) => {
    button.addEventListener("click", () =>
      setActivePage(Number(button.dataset.navCourse), Number(button.dataset.navPage)),
    );
  });

  document.querySelector<HTMLElement>(".course-row.active")?.scrollIntoView({ block: "nearest" });
  document.querySelector<HTMLElement>(".page-row.active")?.scrollIntoView({ block: "nearest" });
}

async function loadArchive(): Promise<void> {
  const indexResponse = await fetch("./classes.json", { cache: "no-store" });
  if (!indexResponse.ok) {
    throw new Error(`classes.json returned ${indexResponse.status}`);
  }

  const classesIndex = (await indexResponse.json()) as ClassesIndex;
  const entries = classesIndex.classes ?? [];
  loadedClasses = (
    await Promise.all(
      entries.map(async (entry) => {
        const response = await fetch(`./${entry.url}`, { cache: "no-store" });
        if (!response.ok) return null;
        return {
          entry,
          manifest: (await response.json()) as ClassManifest,
        };
      }),
    )
  ).filter((item): item is LoadedClass => item !== null);
}

loadArchive()
  .then(render)
  .catch((error: unknown) => {
    renderEmpty(error instanceof Error ? error.message : "Failed to load archive data.");
  });
