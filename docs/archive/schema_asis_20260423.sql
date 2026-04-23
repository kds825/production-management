--
-- PostgreSQL database dump
--

\restrict zCOk25y9lIEbFWcWjSccUSDZSxaSWTzUBCxwOfx3O8ozjbGB4aKii0Yni6Zoe8m

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.9 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    log_id integer NOT NULL,
    run_label character varying(50) NOT NULL,
    stage character varying(10) NOT NULL,
    batch_id integer,
    task_id integer,
    action_type character varying(30) NOT NULL,
    constraints_applied jsonb,
    decision_reason text,
    alternatives_considered jsonb,
    created_at timestamp without time zone
);


--
-- Name: audit_log_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_log_id_seq OWNED BY public.audit_log.log_id;


--
-- Name: constraint_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.constraint_config (
    constraint_id character varying(10) NOT NULL,
    constraint_name character varying(100) NOT NULL,
    category character varying(50) NOT NULL,
    is_enabled boolean,
    priority integer,
    impact_level character varying(10),
    params_json jsonb,
    applicable_processes jsonb,
    implementation_type character varying(20),
    notes text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: constraint_config_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.constraint_config_history (
    history_id integer NOT NULL,
    constraint_id character varying(10) NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by character varying(100),
    old_params_json jsonb,
    new_params_json jsonb NOT NULL
);


--
-- Name: constraint_config_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.constraint_config_history_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: constraint_config_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.constraint_config_history_history_id_seq OWNED BY public.constraint_config_history.history_id;


--
-- Name: customer_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_master (
    customer_code character varying(20) NOT NULL,
    customer_name character varying(100) NOT NULL,
    priority integer,
    due_type character varying(20),
    due_strictness character varying(50),
    require_sample boolean,
    urgency_frequency character varying(20)
);


--
-- Name: decision_criteria; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decision_criteria (
    criteria_id integer NOT NULL,
    criteria_name character varying(100) NOT NULL,
    criteria_value character varying(50) NOT NULL,
    criteria_unit character varying(20),
    description text
);


--
-- Name: decision_criteria_criteria_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.decision_criteria_criteria_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: decision_criteria_criteria_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.decision_criteria_criteria_id_seq OWNED BY public.decision_criteria.criteria_id;


--
-- Name: drum_lot_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drum_lot_master (
    drum_id integer NOT NULL,
    cross_section numeric NOT NULL,
    wire_diameter numeric,
    wire_count integer,
    lot_wire_drawing numeric,
    lot_stranding numeric,
    daily_production numeric,
    setup_time_min numeric,
    drum_weight_ton numeric
);


--
-- Name: drum_lot_master_drum_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.drum_lot_master_drum_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: drum_lot_master_drum_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.drum_lot_master_drum_id_seq OWNED BY public.drum_lot_master.drum_id;


--
-- Name: equipment_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.equipment_master (
    equipment_code character varying(20) NOT NULL,
    equipment_name character varying(50) NOT NULL,
    process_name character varying(30) NOT NULL,
    material_limit character varying(10),
    range_min numeric,
    range_max numeric,
    range_unit character varying(10),
    stranding_method character varying(50),
    color_group character varying(50),
    base_working_hours numeric,
    shift_type character varying(20),
    calendar_rule_code character varying(20)
);


--
-- Name: item_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_master (
    item_code character varying(20) NOT NULL,
    item_name character varying(100),
    spec_abbr character varying(100),
    product_group character varying(50),
    voltage character varying(20),
    conductor_material character varying(10),
    wire_diameter numeric,
    wire_count integer,
    cross_section numeric,
    core_count integer,
    stranding_type character varying(20),
    insulation_type character varying(50),
    sheath_type character varying(50),
    core_colors character varying(100),
    routing_code character varying(20),
    drum_max_length numeric,
    lot_length numeric,
    extra_allowance numeric,
    daily_production numeric,
    unit_weight numeric,
    require_sample boolean,
    is_outsourced boolean
);


--
-- Name: operation_calendar; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operation_calendar (
    calendar_id integer NOT NULL,
    rule_code character varying(20) NOT NULL,
    rule_name character varying(100) NOT NULL,
    day_of_week character varying(50),
    working_hours numeric,
    start_time character varying(10),
    end_time character varying(10),
    deduction_hours numeric,
    specific_date date,
    notes text
);


--
-- Name: operation_calendar_calendar_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.operation_calendar_calendar_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: operation_calendar_calendar_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.operation_calendar_calendar_id_seq OWNED BY public.operation_calendar.calendar_id;


--
-- Name: process_routing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.process_routing (
    routing_code character varying(20) NOT NULL,
    routing_name character varying(100) NOT NULL,
    process_1 character varying(30),
    process_2 character varying(30),
    process_3 character varying(30),
    process_4 character varying(30),
    process_5 character varying(30),
    process_6 character varying(30)
);


--
-- Name: production_batch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_batch (
    batch_id integer NOT NULL,
    run_label character varying(50) NOT NULL,
    sales_order_id character varying(30),
    sales_order_line integer,
    item_code character varying(20),
    routing_code character varying(20),
    process_name character varying(30) NOT NULL,
    equipment_code character varying(20),
    batch_seq integer,
    drum_length_m numeric,
    drum_count integer,
    total_length_m numeric,
    extra_length_m numeric,
    sq_mm2 numeric,
    core_count integer,
    core_colors character varying(100),
    sheath_color character varying(50),
    customer_name character varying(100),
    due_date date,
    customer_priority integer,
    wip_matched_id integer,
    line_speed_mpm numeric,
    setup_time_min numeric,
    estimated_duration_min numeric,
    status character varying(20),
    remarks text,
    product_group character varying(50),
    voltage character varying(20),
    conductor_material character varying(10),
    stranding_type character varying(20),
    created_at timestamp without time zone,
    wip_output_expected_m numeric,
    wip_output_actual_m numeric,
    batch_group character varying(50),
    spec_raw character varying(200),
    is_outsourced integer DEFAULT 0,
    unassign_reason character varying(32),
    parent_run_label character varying(50)
);


--
-- Name: production_batch_batch_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.production_batch_batch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: production_batch_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.production_batch_batch_id_seq OWNED BY public.production_batch.batch_id;


--
-- Name: sales_order; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sales_order (
    order_id character varying(30) NOT NULL,
    order_line integer NOT NULL,
    order_status character varying(20),
    product_group character varying(50),
    voltage character varying(20),
    spec_raw character varying(100),
    item_code character varying(20),
    customer_code character varying(20),
    customer_name character varying(100),
    due_date date,
    due_type character varying(20),
    customer_priority integer,
    drum_length_m numeric,
    drum_count integer,
    ordered_qty_m numeric,
    self_plan_qty_m numeric,
    outsource_qty_m numeric,
    unit_price_krw numeric,
    amount_krw numeric,
    cu_weight_kg numeric,
    al_weight_kg numeric,
    core_count integer,
    core_colors character varying(100),
    sheath_color character varying(50),
    neutral_wire character varying(50),
    use_wip boolean,
    wip_type character varying(20),
    actual_length_m numeric,
    is_outsourced boolean,
    run_label character varying(50),
    created_at timestamp without time zone,
    wip_id integer
);


--
-- Name: schedule_change_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_change_sets (
    change_set_id character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    preview_request_id character varying,
    snapshot_before jsonb NOT NULL,
    snapshot_after jsonb NOT NULL,
    applied_by character varying,
    kind character varying(20) DEFAULT 'cascade'::character varying NOT NULL
);


--
-- Name: schedule_task; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_task (
    task_id integer NOT NULL,
    batch_id integer NOT NULL,
    equipment_code character varying(20) NOT NULL,
    start_datetime timestamp without time zone NOT NULL,
    end_datetime timestamp without time zone NOT NULL,
    predecessor_task_id integer,
    setup_time_min numeric,
    status character varying(20),
    run_label character varying(50),
    created_at timestamp without time zone,
    batch_group character varying(50)
);


--
-- Name: schedule_task_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.schedule_task_task_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: schedule_task_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.schedule_task_task_id_seq OWNED BY public.schedule_task.task_id;


--
-- Name: speed_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.speed_master (
    speed_id integer NOT NULL,
    equipment_code character varying(20) NOT NULL,
    product_type character varying(50),
    cross_section numeric,
    line_speed_mpm numeric,
    line_speed_hr numeric,
    setup_start_min numeric,
    setup_spec_min numeric,
    setup_color_min numeric,
    setup_compound_min numeric,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: speed_master_speed_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.speed_master_speed_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: speed_master_speed_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.speed_master_speed_id_seq OWNED BY public.speed_master.speed_id;


--
-- Name: wip_inventory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wip_inventory (
    wip_id integer NOT NULL,
    process_stage character varying(30),
    voltage_class character varying(20),
    material character varying(10),
    product_name character varying(100),
    spec character varying(100),
    cross_section numeric,
    length_m numeric,
    count integer,
    total_length_m numeric,
    core_colors character varying(100),
    wire_diameter numeric,
    wire_count integer,
    status character varying(20),
    run_label character varying(50),
    created_at timestamp without time zone,
    expected_length_m numeric,
    actual_length_m numeric,
    variance_m numeric,
    source_batch_id integer,
    matched_order_id character varying(30),
    core text
);


--
-- Name: wip_inventory_wip_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wip_inventory_wip_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wip_inventory_wip_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wip_inventory_wip_id_seq OWNED BY public.wip_inventory.wip_id;


--
-- Name: wip_upload_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wip_upload_log (
    upload_id integer NOT NULL,
    canonical_hash character varying(64) NOT NULL,
    raw_file_hash character varying(64),
    run_label character varying(50),
    uploaded_at timestamp without time zone NOT NULL,
    rows_inserted integer DEFAULT 0 NOT NULL,
    rows_updated integer DEFAULT 0 NOT NULL,
    file_name character varying(255)
);


--
-- Name: wip_upload_log_upload_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wip_upload_log_upload_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wip_upload_log_upload_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wip_upload_log_upload_id_seq OWNED BY public.wip_upload_log.upload_id;


--
-- Name: audit_log log_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN log_id SET DEFAULT nextval('public.audit_log_log_id_seq'::regclass);


--
-- Name: constraint_config_history history_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_config_history ALTER COLUMN history_id SET DEFAULT nextval('public.constraint_config_history_history_id_seq'::regclass);


--
-- Name: decision_criteria criteria_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_criteria ALTER COLUMN criteria_id SET DEFAULT nextval('public.decision_criteria_criteria_id_seq'::regclass);


--
-- Name: drum_lot_master drum_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drum_lot_master ALTER COLUMN drum_id SET DEFAULT nextval('public.drum_lot_master_drum_id_seq'::regclass);


--
-- Name: operation_calendar calendar_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_calendar ALTER COLUMN calendar_id SET DEFAULT nextval('public.operation_calendar_calendar_id_seq'::regclass);


--
-- Name: production_batch batch_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch ALTER COLUMN batch_id SET DEFAULT nextval('public.production_batch_batch_id_seq'::regclass);


--
-- Name: schedule_task task_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task ALTER COLUMN task_id SET DEFAULT nextval('public.schedule_task_task_id_seq'::regclass);


--
-- Name: speed_master speed_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.speed_master ALTER COLUMN speed_id SET DEFAULT nextval('public.speed_master_speed_id_seq'::regclass);


--
-- Name: wip_inventory wip_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_inventory ALTER COLUMN wip_id SET DEFAULT nextval('public.wip_inventory_wip_id_seq'::regclass);


--
-- Name: wip_upload_log upload_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_upload_log ALTER COLUMN upload_id SET DEFAULT nextval('public.wip_upload_log_upload_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (log_id);


--
-- Name: constraint_config_history constraint_config_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_config_history
    ADD CONSTRAINT constraint_config_history_pkey PRIMARY KEY (history_id);


--
-- Name: constraint_config constraint_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_config
    ADD CONSTRAINT constraint_config_pkey PRIMARY KEY (constraint_id);


--
-- Name: customer_master customer_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_master
    ADD CONSTRAINT customer_master_pkey PRIMARY KEY (customer_code);


--
-- Name: decision_criteria decision_criteria_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_criteria
    ADD CONSTRAINT decision_criteria_pkey PRIMARY KEY (criteria_id);


--
-- Name: drum_lot_master drum_lot_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drum_lot_master
    ADD CONSTRAINT drum_lot_master_pkey PRIMARY KEY (drum_id);


--
-- Name: equipment_master equipment_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment_master
    ADD CONSTRAINT equipment_master_pkey PRIMARY KEY (equipment_code);


--
-- Name: item_master item_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_master
    ADD CONSTRAINT item_master_pkey PRIMARY KEY (item_code);


--
-- Name: operation_calendar operation_calendar_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_calendar
    ADD CONSTRAINT operation_calendar_pkey PRIMARY KEY (calendar_id);


--
-- Name: process_routing process_routing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.process_routing
    ADD CONSTRAINT process_routing_pkey PRIMARY KEY (routing_code);


--
-- Name: production_batch production_batch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch
    ADD CONSTRAINT production_batch_pkey PRIMARY KEY (batch_id);


--
-- Name: sales_order sales_order_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order
    ADD CONSTRAINT sales_order_pkey PRIMARY KEY (order_id, order_line);


--
-- Name: schedule_change_sets schedule_change_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_sets
    ADD CONSTRAINT schedule_change_sets_pkey PRIMARY KEY (change_set_id);


--
-- Name: schedule_task schedule_task_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task
    ADD CONSTRAINT schedule_task_pkey PRIMARY KEY (task_id);


--
-- Name: speed_master speed_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.speed_master
    ADD CONSTRAINT speed_master_pkey PRIMARY KEY (speed_id);


--
-- Name: wip_inventory wip_inventory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_inventory
    ADD CONSTRAINT wip_inventory_pkey PRIMARY KEY (wip_id);


--
-- Name: wip_upload_log wip_upload_log_canonical_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_upload_log
    ADD CONSTRAINT wip_upload_log_canonical_hash_key UNIQUE (canonical_hash);


--
-- Name: wip_upload_log wip_upload_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_upload_log
    ADD CONSTRAINT wip_upload_log_pkey PRIMARY KEY (upload_id);


--
-- Name: idx_wip_source_batch_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_wip_source_batch_unique ON public.wip_inventory USING btree (source_batch_id) WHERE (source_batch_id IS NOT NULL);


--
-- Name: ix_audit_log_run_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_run_label ON public.audit_log USING btree (run_label);


--
-- Name: ix_constraint_config_history_constraint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_constraint_config_history_constraint_id ON public.constraint_config_history USING btree (constraint_id);


--
-- Name: ix_operation_calendar_rule_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_calendar_rule_code ON public.operation_calendar USING btree (rule_code);


--
-- Name: ix_production_batch_batch_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_batch_batch_group ON public.production_batch USING btree (batch_group);


--
-- Name: ix_production_batch_parent_run_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_batch_parent_run_label ON public.production_batch USING btree (parent_run_label);


--
-- Name: ix_production_batch_run_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_batch_run_label ON public.production_batch USING btree (run_label);


--
-- Name: ix_production_batch_unassigned; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_batch_unassigned ON public.production_batch USING btree (batch_group) WHERE ((status)::text = 'unassigned'::text);


--
-- Name: ix_schedule_change_sets_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_change_sets_created_at ON public.schedule_change_sets USING btree (created_at);


--
-- Name: ix_schedule_change_sets_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_change_sets_kind ON public.schedule_change_sets USING btree (kind);


--
-- Name: ix_schedule_task_batch_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_task_batch_group ON public.schedule_task USING btree (batch_group);


--
-- Name: ix_schedule_task_run_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_task_run_label ON public.schedule_task USING btree (run_label);


--
-- Name: ix_schedule_task_unassigned; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedule_task_unassigned ON public.schedule_task USING btree (batch_id) WHERE ((status)::text = 'unassigned'::text);


--
-- Name: audit_log audit_log_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.production_batch(batch_id);


--
-- Name: audit_log audit_log_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.schedule_task(task_id) ON DELETE CASCADE;


--
-- Name: constraint_config_history constraint_config_history_constraint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_config_history
    ADD CONSTRAINT constraint_config_history_constraint_id_fkey FOREIGN KEY (constraint_id) REFERENCES public.constraint_config(constraint_id);


--
-- Name: item_master item_master_routing_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_master
    ADD CONSTRAINT item_master_routing_code_fkey FOREIGN KEY (routing_code) REFERENCES public.process_routing(routing_code);


--
-- Name: production_batch production_batch_equipment_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch
    ADD CONSTRAINT production_batch_equipment_code_fkey FOREIGN KEY (equipment_code) REFERENCES public.equipment_master(equipment_code);


--
-- Name: production_batch production_batch_item_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch
    ADD CONSTRAINT production_batch_item_code_fkey FOREIGN KEY (item_code) REFERENCES public.item_master(item_code);


--
-- Name: production_batch production_batch_routing_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch
    ADD CONSTRAINT production_batch_routing_code_fkey FOREIGN KEY (routing_code) REFERENCES public.process_routing(routing_code);


--
-- Name: production_batch production_batch_wip_matched_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_batch
    ADD CONSTRAINT production_batch_wip_matched_id_fkey FOREIGN KEY (wip_matched_id) REFERENCES public.wip_inventory(wip_id);


--
-- Name: sales_order sales_order_customer_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order
    ADD CONSTRAINT sales_order_customer_code_fkey FOREIGN KEY (customer_code) REFERENCES public.customer_master(customer_code);


--
-- Name: sales_order sales_order_item_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order
    ADD CONSTRAINT sales_order_item_code_fkey FOREIGN KEY (item_code) REFERENCES public.item_master(item_code);


--
-- Name: sales_order sales_order_wip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_order
    ADD CONSTRAINT sales_order_wip_id_fkey FOREIGN KEY (wip_id) REFERENCES public.wip_inventory(wip_id);


--
-- Name: schedule_task schedule_task_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task
    ADD CONSTRAINT schedule_task_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.production_batch(batch_id);


--
-- Name: schedule_task schedule_task_equipment_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task
    ADD CONSTRAINT schedule_task_equipment_code_fkey FOREIGN KEY (equipment_code) REFERENCES public.equipment_master(equipment_code);


--
-- Name: schedule_task schedule_task_predecessor_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_task
    ADD CONSTRAINT schedule_task_predecessor_task_id_fkey FOREIGN KEY (predecessor_task_id) REFERENCES public.schedule_task(task_id);


--
-- Name: speed_master speed_master_equipment_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.speed_master
    ADD CONSTRAINT speed_master_equipment_code_fkey FOREIGN KEY (equipment_code) REFERENCES public.equipment_master(equipment_code);


--
-- Name: wip_inventory wip_inventory_source_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wip_inventory
    ADD CONSTRAINT wip_inventory_source_batch_id_fkey FOREIGN KEY (source_batch_id) REFERENCES public.production_batch(batch_id);


--
-- PostgreSQL database dump complete
--

\unrestrict zCOk25y9lIEbFWcWjSccUSDZSxaSWTzUBCxwOfx3O8ozjbGB4aKii0Yni6Zoe8m

