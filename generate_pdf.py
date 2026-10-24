import sys
import os

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
    )
    from reportlab.pdfgen import canvas
except ImportError:
    print("ReportLab not found. Installing reportlab...")
    os.system(f"{sys.executable} -m pip install reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
    )
    from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#718096"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 750, "DisasterGraph: Micro-Project Review Reference Guide")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)
            
        # Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 35, page_text)
        self.drawString(54, 35, "Department of Computer Science & Engineering | Micro-Project 2026-2027")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 48, 558, 48)
        self.restoreState()

def build_pdf(filename):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    # Custom Palette
    PRIMARY = colors.HexColor("#1A365D")   # Deep Navy
    SECONDARY = colors.HexColor("#2B6CB0") # Slate Blue
    ACCENT = colors.HexColor("#C53030")    # Crimson Accent
    DARK_TEXT = colors.HexColor("#2D3748")
    LIGHT_BG = colors.HexColor("#F7FAFC")
    BORDER_COLOR = colors.HexColor("#E2E8F0")
    CALLOUT_BG = colors.HexColor("#EDF2F7")
    QA_BG = colors.HexColor("#F0F4F8")
    
    # Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=PRIMARY,
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=SECONDARY,
        spaceAfter=12
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=SECONDARY,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12.5,
        textColor=DARK_TEXT,
        spaceAfter=5
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12.5,
        textColor=DARK_TEXT,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3
    )

    callout_style = ParagraphStyle(
        'Callout_Text',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#2C5282")
    )

    q_style = ParagraphStyle(
        'Question_Text',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12.5,
        textColor=colors.HexColor("#9B2C2C"),
        spaceAfter=2
    )

    a_style = ParagraphStyle(
        'Answer_Text',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=DARK_TEXT,
        spaceAfter=2
    )

    th_style = ParagraphStyle(
        'TH_Text',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.white
    )

    td_style = ParagraphStyle(
        'TD_Text',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=DARK_TEXT
    )

    story = []

    # Title Block
    story.append(Paragraph("DisasterGraph: Real-Time Disaster Emergency Response Orchestration", title_style))
    story.append(Paragraph("<b>Micro-Project Review Reference & Defense Guide</b> | Department of CSE, BVCOE New Delhi", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceBefore=0, spaceAfter=8))

    # Meta Info Card
    meta_data = [
        [Paragraph("<b>Mentor:</b> Mr. Vijay Kumar (Assistant Professor)", body_style),
         Paragraph("<b>Team Members:</b> Jatin Berwal (013), Vishad Srivastava (016), Devika Malhotra (373), Anushka Prakash (193)", body_style)],
        [Paragraph("<b>Project Phase:</b> Literature Survey, Research & Architectural Formulation", body_style),
         Paragraph("<b>Review Intent:</b> Theoretical justification, literature gaps, & planned methodology defense", body_style)]
    ]
    meta_table = Table(meta_data, colWidths=[240, 264])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 8))

    # Framing Note Callout
    framing_text = (
        "<b>Important Presentation Framing:</b> Frame all discussions around active research, literature survey findings, "
        "and planned architectural hypotheses (e.g., <i>'Our literature survey indicates...', 'We are evaluating whether...', "
        "'One design approach we propose to explore...'</i>). The system is currently in the design & review stage."
    )
    framing_table = Table([[Paragraph(framing_text, callout_style)]], colWidths=[504])
    framing_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), CALLOUT_BG),
        ('BOX', (0,0), (-1,-1), 1, SECONDARY),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(framing_table)
    story.append(Spacer(1, 10))

    # --- SECTION 1: INTRODUCTION ---
    story.append(Paragraph("1. Introduction & Problem Domain", h1_style))
    story.append(Paragraph("<b>The Disaster-Response Challenge:</b> Rapid-onset disasters (urban floods, wildfires, industrial hazmat/chemical gas leaks) cause catastrophic loss of life and billions in damages. The first <b>'Golden Hour'</b> post-incident is critical: survival rates decline exponentially if extraction, trauma care, and plume avoidance are delayed.", body_style))
    story.append(Paragraph("<b>Current Command Center Bottlenecks:</b> Traditional emergency systems operate across disjointed silos (separate weather screens, static municipal GIS layers, manual phone trees, GPS feeds). Human dispatchers face severe cognitive fatigue when manually synthesizing complex spatial-temporal dependencies under high stress.", body_style))
    story.append(Paragraph("<b>The DisasterGraph Direction:</b> DisasterGraph investigates the fusion of <b>Property Graph Databases (Neo4j)</b> for instant topological pathfinding, <b>Multi-Modal Satellite Feeds</b> for dynamic environmental hazard mapping, and <b>Guardrailed LLM Agents</b> for automated, explainable dispatch triage.", body_style))
    story.append(Paragraph("<b>UN Sustainable Development Goals (SDG) Alignment:</b>", body_style))
    story.append(Paragraph("• <b>SDG 11 (Sustainable Cities - Target 11.5):</b> Mitigating urban disaster casualties and protecting vulnerable communities via dynamic route optimization.", bullet_style))
    story.append(Paragraph("• <b>SDG 13 (Climate Action - Target 13.1):</b> Building adaptive urban resilience against escalating climate-induced hazard events.", bullet_style))
    story.append(Paragraph("• <b>SDG 3 (Good Health & Well-Being - Target 3.6 & 3.d):</b> Accelerating critical trauma care dispatch and avoiding toxic atmospheric exposure.", bullet_style))
    
    # Q&A Box 1
    qa1_data = [
        [Paragraph("<b>Mentor Q:</b> <i>Why do we need an LLM in a life-or-death emergency system? Isn't rule-based code safer?</i>", q_style)],
        [Paragraph("<b>Answer:</b> Rule-based algorithms excel at mathematical shortest paths, which is why our Tier 2 Graph Engine handles all deterministic calculations. However, stressed human operators struggle to synthesize multi-variable tabular data quickly. We propose the LLM strictly as an <b>explainable orchestration layer</b> — translating verified graph states into structured JSON manifests and concise natural-language action briefs (e.g., 'Rerouting Ambulance 4 via North Bridge due to expanding toxic gas plume'). The LLM does not invent paths; it synthesizes deterministic facts.", a_style)],
        [Paragraph("<b>Mentor Q:</b> <i>How does DisasterGraph address the Golden Hour differently from standard 911 / Dial 112?</i>", q_style)],
        [Paragraph("<b>Answer:</b> Traditional 911 uses sequential, human-in-the-loop triage (call intake ➔ check hospital availability in DB 1 ➔ check police in DB 2 ➔ manual radio dispatch). DisasterGraph models the entire urban ecosystem as a connected property graph, updating road penalties automatically when satellite sensors detect hazards, and outputting actionable dispatch proposals in seconds.", a_style)]
    ]
    qa1_table = Table(qa1_data, colWidths=[504])
    qa1_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), QA_BG),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,1), (0,1), 0.5, colors.HexColor("#E2E8F0"))
    ]))
    story.append(Spacer(1, 4))
    story.append(qa1_table)
    story.append(Spacer(1, 10))

    # --- SECTION 2: SYSTEM LAYERS ---
    story.append(Paragraph("2. System Layers Being Explored", h1_style))
    layers_data = [
        [Paragraph("Layer", th_style), Paragraph("Technology Explored", th_style), Paragraph("Conceptual Role & Rationale", th_style), Paragraph("Alternatives & Trade-offs", th_style)],
        [
            Paragraph("<b>Data Layer</b>", td_style),
            Paragraph("NASA FIRMS (MODIS/VIIRS), Sentinel-5P (TROPOMI), OpenStreetMap (OSM)", td_style),
            Paragraph("Automated ETL ingesting near-real-time thermal hotspots (FRP), NO2/SO2 rasters, and street topology into geo-referenced features.", td_style),
            Paragraph("<b>Google Earth Engine:</b> Higher latency for streaming ETL.<br/><b>Commercial APIs:</b> Restrictive access to raw topological road graphs.", td_style)
        ],
        [
            Paragraph("<b>Graph Layer</b>", td_style),
            Paragraph("Neo4j AuraDB & Cypher Queries", td_style),
            Paragraph("Property graph with index-free adjacency for sub-second multi-hop traversals (Hazard ➔ Blocked Road ➔ Victims ➔ Shelters).", td_style),
            Paragraph("<b>PostgreSQL/PostGIS:</b> Multi-table recursive joins degrade under load ($O(N^k)$).<br/><b>ArangoDB:</b> Neo4j offers richer Graph Data Science tools.", td_style)
        ],
        [
            Paragraph("<b>AI Layer</b>", td_style),
            Paragraph("Claude 3.5 Sonnet (OpenRouter) + JSON Schema", td_style),
            Paragraph("Autonomous context-aware triage assignment and structured dispatch generation constrained by strict JSON schema guardrails.", td_style),
            Paragraph("<b>Local Llama 3:</b> High VRAM/compute requirements.<br/><b>Rule Engines:</b> Inflexible with compounded, multi-hazard scenarios.", td_style)
        ],
        [
            Paragraph("<b>Interface Layer</b>", td_style),
            Paragraph("FastAPI, WebSockets, React/Leaflet, Telegram Bot API", td_style),
            Paragraph("Real-time bi-directional streaming risk map for command centers, with automated tactical push notifications to field officers.", td_style),
            Paragraph("<b>REST Polling:</b> High latency & HTTP overhead.<br/><b>Custom Native Mobile App:</b> Heavy deployment footprint vs instant Telegram bot.", td_style)
        ]
    ]
    layers_table = Table(layers_data, colWidths=[65, 110, 185, 144])
    layers_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_BG])
    ]))
    story.append(layers_table)
    story.append(Spacer(1, 10))

    # --- SECTION 3: LITERATURE SURVEY ---
    story.append(Paragraph("3. Detailed Literature Survey", h1_style))
    
    lit_papers = [
        ("Davies et al. (2018) — Near-Real-Time Fire Detection via NASA FIRMS",
         "Evaluated operational performance of MODIS and VIIRS satellite sensors for active wildfire detection via thermal band radiance ($3.7\\,\\mu\\text{m}$ and $11\\,\\mu\\text{m}$) and Fire Radiative Power (FRP).",
         "Proved satellite radiometric sensors provide highly accurate, near-real-time wildfire boundary detection across vast geographical terrains within 1 to 3 hours.",
         "FIRMS acts purely as a passive observation layer. Outputs raw coordinate rasters/points with no coupling to urban road graphs, emergency services, or automated dispatch systems."),
        
        ("Veefkind et al. (2012) — Sentinel-5P TROPOMI for Atmospheric Gas Monitoring",
         "Introduced the TROPOMI UV-VIS-NIR-SWIR spectrometer on ESA Sentinel-5P measuring vertical column densities of trace gases ($NO_2, SO_2, CO, CH_4$, aerosols) at $5.5 \\times 3.5\\,\\text{km}^2$ resolution.",
         "Demonstrated exceptional sensitivity in tracking industrial chemical gas emissions, volcanic plumes, and regional toxic gas dispersion.",
         "Designed strictly for atmospheric and climate research. Plume boundaries have never been integrated into dynamic street-level evacuation path-planning or toxic-zone avoidance engines."),
        
        ("Zheng et al. (2021) — Neo4j Graph Schemas for Flood Evacuation Routing",
         "Modeled urban road networks, flood depth points, and evacuation shelters in Neo4j; benchmarked multi-hop evacuation pathfinding queries against relational database (RDBMS) SQL joins.",
         "Graph traversals using index-free adjacency outperformed relational multi-join SQL by 10x to 100x as network depth and dependency complexity grew.",
         "Operated on static topological graphs and pre-computed inundation scenarios. Did not support real-time dynamic edge mutation from live satellite streams, nor autonomous AI reasoning."),
        
        ("Wang et al. (2024) — Survey on LLMs in Command-and-Control (C2) Architectures",
         "Assessed the operational viability, latency, and failure modes of Large Language Models in military and civilian emergency command-and-control workflows.",
         "LLMs exhibit remarkable capabilities for multi-modal situational summarization, semantic reasoning, and translating complex status logs into natural-language action briefs.",
         "Unconstrained LLMs suffer from severe stochastic hallucinations and non-deterministic logic errors. Authors explicitly recommend grounding LLMs on a deterministic knowledge graph ground truth.")
    ]

    for title, method, finding, gap in lit_papers:
        p_data = [
            [Paragraph(f"<b>{title}</b>", h2_style)],
            [Paragraph(f"• <b>Method / Scope:</b> {method}", bullet_style)],
            [Paragraph(f"• <b>Key Finding:</b> {finding}", bullet_style)],
            [Paragraph(f"• <b>Identified Limitation / Gap:</b> {gap}", bullet_style)]
        ]
        p_table = Table(p_data, colWidths=[504])
        p_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
            ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'TOP')
        ]))
        story.append(p_table)
        story.append(Spacer(1, 5))

    # Q&A Box Literature
    qa2_data = [
        [Paragraph("<b>Mentor Q:</b> <i>Why combine atmospheric monitoring (Veefkind / Sentinel-5P) with road routing?</i>", q_style)],
        [Paragraph("<b>Answer:</b> Most flood or wildfire routing systems only consider physical road blocks (flood depth or fire). However, hazmat tanker accidents or industrial fires produce toxic gas clouds ($NO_2, SO_2$). Veefkind et al. proved satellite spectrometers can map these concentrations. Our architecture integrates these rasters into dynamic graph penalties so ambulances aren't directed through toxic plumes.", a_style)],
        [Paragraph("<b>Mentor Q:</b> <i>What is the core architectural lesson from Wang et al. (2024)?</i>", q_style)],
        [Paragraph("<b>Answer:</b> Wang et al. established that LLMs must never compute raw mathematical or spatial paths directly due to hallucination risks. Instead, they must sit on top of a deterministic graph layer that provides verified mathematical ground truth, using the LLM strictly to orchestrate and articulate decisions.", a_style)]
    ]
    qa2_table = Table(qa2_data, colWidths=[504])
    qa2_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), QA_BG),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,1), (0,1), 0.5, colors.HexColor("#E2E8F0"))
    ]))
    story.append(Spacer(1, 4))
    story.append(qa2_table)
    story.append(Spacer(1, 10))

    # --- SECTION 4: COMPARATIVE ANALYSIS ---
    story.append(Paragraph("4. Literature Survey — Comparative Analysis", h1_style))
    comp_data = [
        [Paragraph("Feature / Dimension", th_style), Paragraph("GIS Dashboards", th_style), Paragraph("RDBMS (SQL)", th_style), Paragraph("Static Graphs", th_style), Paragraph("DisasterGraph (Proposed)", th_style)],
        [
            Paragraph("<b>Real-Time Satellite ETL</b>", td_style),
            Paragraph("Manual layer import (GeoJSON/Shapefile)", td_style),
            Paragraph("Batch ingestion via heavy relational tables", td_style),
            Paragraph("Periodic scripting / manual updates", td_style),
            Paragraph("Automated multi-modal streaming ETL pipeline", td_style)
        ],
        [
            Paragraph("<b>Graph Traversal Engine</b>", td_style),
            Paragraph("None (Visual overlay only)", td_style),
            Paragraph("Slow multi-join SQL ($O(N^k)$ complexity)", td_style),
            Paragraph("Single-threaded Cypher queries", td_style),
            Paragraph("Parallel graph engine with Index-Free Adjacency", td_style)
        ],
        [
            Paragraph("<b>Dynamic Road Blockages</b>", td_style),
            Paragraph("Static visual maps", td_style),
            Paragraph("Hard-coded tables, lock contention", td_style),
            Paragraph("Manual edge deletion & rebuilds", td_style),
            Paragraph("Instant dynamic path filtering & fallback routes", td_style)
        ],
        [
            Paragraph("<b>Victim Triage Mechanism</b>", td_style),
            Paragraph("Manual operator sorting", td_style),
            Paragraph("Simple FIFO or single-variable sorting", td_style),
            Paragraph("Basic rule heuristics", td_style),
            Paragraph("Vulnerability-weighted algorithmic ranking", td_style)
        ],
        [
            Paragraph("<b>Autonomous AI Reasoning</b>", td_style),
            Paragraph("Absent", td_style),
            Paragraph("Absent", td_style),
            Paragraph("Rule-based IF-THEN scripts", td_style),
            Paragraph("LLM agent + JSON schema guardrails", td_style)
        ]
    ]
    comp_table = Table(comp_data, colWidths=[90, 100, 100, 104, 110])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_BG])
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 10))

    # --- SECTION 5: RESEARCH GAPS ---
    story.append(Paragraph("5. Research Gaps Grounded in Literature", h1_style))
    gaps_content = [
        ("Gap 1: The Relational Multi-Hop Computational Bottleneck",
         "Relational schemas represent entity connections via foreign keys. In an emergency evaluation involving recursive dependency chains (Zone ➔ Road Hazard ➔ Casualty Triage ➔ Available Ambulances ➔ Open Trauma Beds), SQL joins incur exponential search complexity ($O(N^k)$). During peak disaster events, this triggers severe database locks and unviable query latency."),
        ("Gap 2: Absence of Grounded, Explainable Hybrid AI Orchestration",
         "Pure algorithmic dispatchers produce rigid numerical output without human-interpretable rationale, while unconstrained LLMs pose severe risks of stochastic hallucination. Existing literature lacks an integrated hybrid architecture where deterministic graph algorithms supply verified topological ground truth, and an LLM converts that state into structured, explainable dispatch directives."),
        ("Gap 3: Operational Isolation of Multi-Modal Satellite Telemetry",
         "High-resolution environmental satellite data (NASA FIRMS, Sentinel-5P) is currently confined to scientific analysis and passive web dashboards. There is an absence of automated streaming ETL pipelines that ingest active thermal points and atmospheric gas rasters directly into real-time routing engines for immediate field dispatch.")
    ]
    for g_title, g_desc in gaps_content:
        story.append(Paragraph(f"• <b>{g_title}:</b> {g_desc}", bullet_style))
    story.append(Spacer(1, 8))

    # --- SECTION 6: OBJECTIVES ---
    story.append(Paragraph("6. Project Objectives (Planned Research & Development Goals)", h1_style))
    objs = [
        "<b>Objective 1:</b> Design scalable ETL ingestion pipelines to continuously normalize NASA FIRMS thermal anomaly points, Sentinel-5P gas rasters, and OpenStreetMap (OSM) topological networks.",
        "<b>Objective 2:</b> Formulate a robust Property Graph Schema (Person, Zone, Resource, Route, DisasterEvent, Officer) capable of sub-second vertex and edge property mutations under live disaster conditions.",
        "<b>Objective 3:</b> Implement algorithmic Cypher queries for dynamic severity propagation, vulnerability-weighted casualty triage ranking, and instant passable fallback route discovery.",
        "<b>Objective 4:</b> Build an autonomous LLM agent workflow that ingests verified graph states and produces structured, explainable dispatch decisions enforced by strict Pydantic/JSON-schema guardrails.",
        "<b>Objective 5:</b> Develop a live command-center dashboard (FastAPI + React/Leaflet) with automated Telegram push alerts delivering actionable tactical cards to field responders."
    ]
    for o in objs:
        story.append(Paragraph(f"• {o}", bullet_style))
    story.append(Spacer(1, 10))

    # --- SECTION 7: PROPOSED METHODOLOGY ---
    story.append(Paragraph("7. Proposed Methodology (4-Tier Architecture)", h1_style))
    tiers = [
        ("Tier 1: Multi-Modal Data Ingestion",
         "Automated ETL fetching NASA FIRMS thermal points, Sentinel-5P gas rasters, and OSM road topology via Overpass API. Geo-coordinates are transformed into spatial point properties within the graph database."),
        ("Tier 2: Graph Database Engine (Neo4j)",
         "Maintains entities (:Person, :Zone, :Resource, :Route, :DisasterEvent, :Officer). Executes real-time severity propagation queries: as hazard intensity updates, affected road segments are assigned infinite traversal penalties, automatically rerouting active resources."),
        ("Tier 3: Autonomous LLM Agent Loop",
         "Extracts pruned sub-graph contexts (critical victims, nearby unassigned ambulances, passable corridors). Claude 3.5 Sonnet processes the context under strict JSON-schema guardrails to output deterministic allocation manifests and natural-language justifications."),
        ("Tier 4: Visualization & Alerting Engine",
         "FastAPI backend with asynchronous WebSocket channels pushing live risk map updates to a React/Leaflet dashboard, while dispatch webhooks push immediate tactical alert cards to field officers via Telegram.")
    ]
    for t_title, t_desc in tiers:
        story.append(Paragraph(f"<b>{t_title}:</b> {t_desc}", body_style))
    story.append(Spacer(1, 8))

    # --- SECTION 8: PERFORMANCE COMPARISON CLAIMS ---
    story.append(Paragraph("8. Performance Comparison: Theoretical & Literature Basis", h1_style))
    perf_data = [
        [Paragraph("Metric / Dimension", th_style), Paragraph("Traditional / Existing Approach", th_style), Paragraph("DisasterGraph (Proposed)", th_style), Paragraph("Theoretical & Literature Basis", th_style)],
        [
            Paragraph("<b>Query & Route Discovery</b>", td_style),
            Paragraph("Multi-join SQL / manual cross-referencing (Slow)", td_style),
            Paragraph("Parallel graph traversal (10-100x speedup expected)", td_style),
            Paragraph("<b>Index-Free Adjacency:</b> Direct memory pointer traversals ($O(1)$ per hop) vs global B-tree index joins (Zheng et al., 2021).", td_style)
        ],
        [
            Paragraph("<b>Victim Triage</b>", td_style),
            Paragraph("FIFO queue or simple static sorting", td_style),
            Paragraph("Vulnerability-weighted algorithmic ranking", td_style),
            Paragraph("Multi-variable composite scoring (hazard proximity, medical state, trapped time) prioritizes critical life-safety.", td_style)
        ],
        [
            Paragraph("<b>Road Blockage Handling</b>", td_style),
            Paragraph("Static maps, manual edge deletion", td_style),
            Paragraph("Instant dynamic path filtering & fallback routing", td_style),
            Paragraph("Edge property invalidation (`cost = Infinity`) excludes blocked segments without requiring full network graph re-indexing.", td_style)
        ],
        [
            Paragraph("<b>Dispatch Decision Synthesis</b>", td_style),
            Paragraph("Manual synthesis by human dispatcher (Minutes)", td_style),
            Paragraph("Autonomous LLM agent + JSON guardrails (Seconds)", td_style),
            Paragraph("Extracts pre-computed graph facts and generates structured manifests in parallel, mitigating operator cognitive overload.", td_style)
        ],
        [
            Paragraph("<b>Field Officer Alerting</b>", td_style),
            Paragraph("VHF radio broadcasts / manual phone calls", td_style),
            Paragraph("Automated Telegram push notifications", td_style),
            Paragraph("Asynchronous webhook queues push structured coordinates directly to field devices, eliminating call center queues.", td_style)
        ]
    ]
    perf_table = Table(perf_data, colWidths=[90, 110, 120, 184])
    perf_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 4),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, LIGHT_BG])
    ]))
    story.append(perf_table)
    story.append(Spacer(1, 10))

    # --- SECTION 9: GLOSSARY ---
    story.append(Paragraph("9. Key Technical Glossary", h1_style))
    glossary_items = [
        ("Property Graph", "A database model organizing data as nodes (entities), relationships (directed edges), and key-value properties stored directly on nodes and edges."),
        ("Cypher Query Language", "A declarative graph query language optimized for pattern matching and traversing complex multi-hop relationships in Neo4j."),
        ("Index-Free Adjacency (IFA)", "A graph database architecture where each node holds direct memory pointers to adjacent neighbors, ensuring query performance is independent of total graph size."),
        ("Multi-Hop Traversal", "Navigating sequential connected relationships (Node A ➔ B ➔ C ➔ D) in a single unified graph query execution."),
        ("TROPOMI Spectrometer", "The Tropospheric Monitoring Instrument on ESA's Sentinel-5P satellite measuring atmospheric column concentrations of trace gases (NO2, SO2)."),
        ("Fire Radiative Power (FRP)", "A satellite radiometric measurement (in Megawatts) quantifying the instantaneous thermal energy release of active fires."),
        ("JSON-Schema Guardrails", "Constraint validation mechanism ensuring LLM generation strictly adheres to predefined data structures before execution."),
        ("Zero-Shot Dispatch Reasoning", "The ability of an LLM to evaluate complex emergency operational contexts and produce valid decisions without task-specific fine-tuning.")
    ]
    for term, definition in glossary_items:
        story.append(Paragraph(f"• <b>{term}:</b> {definition}", bullet_style))
    story.append(Spacer(1, 10))

    # --- SECTION 10: TRICKY MENTOR DEFENSE ---
    story.append(Paragraph("10. Anticipated Tricky Mentor Questions & Defense Strategies", h1_style))
    tricky_qa = [
        ("Q1: Why not just use PostgreSQL + PostGIS? It's mature and handles spatial data easily.",
         "PostGIS is outstanding for 2D geometric filtering (e.g., 'find all hospitals within 5km'). However, emergency orchestration is fundamentally a connected relationship and dependency problem. Answering 'Find an ambulance from Hospital A to Victim B avoiding Hazard C while verifying Shelter D has capacity' requires 5+ table joins in SQL ($O(N^k)$ complexity). Neo4j uses Index-Free Adjacency ($O(1)$ relationship traversal), scaling smoothly even under dense urban casualty networks."),
        
        ("Q2: How do you guarantee the LLM will not hallucinate invalid ambulances or dangerous routes?",
         "We enforce a strict separation of concerns: (1) The LLM never calculates raw paths or proximity — that is handled mathematically by Neo4j graph algorithms. (2) The LLM only receives verified, pre-computed graph subgraphs. (3) Outputs are validated against strict Pydantic JSON schemas with enum validation on active database IDs. (4) The system acts as a Decision Support System with human dispatcher review."),
        
        ("Q3: Satellite data has latency (Sentinel-5P daily pass, FIRMS 1-3 hrs). How can you claim 'Real-Time' response?",
         "We distinguish between macro-environmental awareness and micro-operational response. Satellite telemetry provides macro-boundary tracking (wildfire progression, toxic plume spread over hours), while our property graph processes micro-level updates (911 caller locations, GPS pings, active road blockages) in milliseconds. Fusing both enables macro situational awareness with sub-second tactical responsiveness."),
        
        ("Q4: What happens if cellular networks fail in the disaster area?",
         "Our initial architecture uses Telegram because of its lightweight payload and asynchronous message queueing. In our roadmap, we plan to evaluate ad-hoc mesh networking protocols (e.g., LoRaWAN) and local SQLite graph caching on ruggedized field devices for completely severed network zones.")
    ]
    for q_t, ans_t in tricky_qa:
        q_box = [
            [Paragraph(f"<b>Mentor:</b> <i>{q_t}</i>", q_style)],
            [Paragraph(f"<b>Defense Strategy:</b> {ans_t}", a_style)]
        ]
        q_table = Table(q_box, colWidths=[504])
        q_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), QA_BG),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
            ('PADDING', (0,0), (-1,-1), 5),
            ('VALIGN', (0,0), (-1,-1), 'TOP')
        ]))
        story.append(q_table)
        story.append(Spacer(1, 4))

    # --- SECTION 11: TEAM DIVISION ---
    story.append(Spacer(1, 6))
    story.append(Paragraph("11. Suggested 4-Member Team Presentation Division", h1_style))
    div_data = [
        [Paragraph("Speaker 1: Context & Problem", th_style), Paragraph("Speaker 2: Literature & Gaps", th_style), Paragraph("Speaker 3: Proposed Methodology", th_style), Paragraph("Speaker 4: Comparative Analysis & Defense", th_style)],
        [
            Paragraph("• Slides 1-4<br/>• Problem Context & Golden Hour<br/>• Limitations of Manual C2<br/>• SDG 11/13/3 Alignment<br/>• 4 System Layers Scope", td_style),
            Paragraph("• Slides 5 & 7<br/>• Davies, Veefkind, Zheng, Wang papers<br/>• 3 Core Research Gaps<br/>• Grounding in prior literature", td_style),
            Paragraph("• Slides 8 & 9<br/>• 5 Project Objectives<br/>• 4-Tier Architecture<br/>• Graph Schema & Severity Propagation<br/>• JSON-Guarded LLM loop", td_style),
            Paragraph("• Slides 6 & 10<br/>• Comparative Analysis Table<br/>• Theoretical Performance claims (IFA vs SQL)<br/>• Answering Tricky Questions (PostGIS vs Graph, LLMs)", td_style)
        ]
    ]
    div_table = Table(div_data, colWidths=[126, 126, 126, 126])
    div_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), SECONDARY),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [LIGHT_BG])
    ]))
    story.append(div_table)

    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated PDF: {filename}")

if __name__ == "__main__":
    output_pdf = os.path.join(os.getcwd(), "DisasterGraph_MicroProject_Review_Guide.pdf")
    build_pdf(output_pdf)
