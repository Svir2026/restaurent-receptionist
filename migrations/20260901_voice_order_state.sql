-- Test-branch migration. Do not apply to production without a separate review.

create table if not exists public.voice_order_sessions (
    restaurant_id uuid not null references public.restaurants(id),
    conversation_id text not null,
    revision bigint not null check (revision >= 1),
    state jsonb not null,
    expires_at timestamptz not null default (now() + interval '24 hours'),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (restaurant_id, conversation_id)
);

create table if not exists public.voice_order_events (
    restaurant_id uuid not null,
    conversation_id text not null,
    event_id text not null,
    request_hash text not null,
    state_revision bigint not null check (state_revision >= 1),
    response jsonb not null,
    created_at timestamptz not null default now(),
    primary key (restaurant_id, conversation_id, event_id),
    foreign key (restaurant_id, conversation_id)
        references public.voice_order_sessions(restaurant_id, conversation_id)
        on delete cascade
);

create index if not exists voice_order_sessions_expires_at_idx
    on public.voice_order_sessions (expires_at);

alter table public.voice_order_sessions enable row level security;
alter table public.voice_order_events enable row level security;

create or replace function public.save_voice_order_transition(
    p_restaurant_id uuid,
    p_conversation_id text,
    p_expected_revision bigint,
    p_state jsonb,
    p_event_id text,
    p_request_hash text,
    p_response jsonb
)
returns table (
    idempotent_replay boolean,
    result_state jsonb,
    result_response jsonb
)
language plpgsql
security definer
set search_path = public
as $$
declare
    existing_event public.voice_order_events%rowtype;
    existing_session public.voice_order_sessions%rowtype;
    next_revision bigint := p_expected_revision + 1;
begin
    if p_restaurant_id is null
       or nullif(btrim(p_conversation_id), '') is null
       or nullif(btrim(p_event_id), '') is null
       or p_expected_revision < 0 then
        raise exception 'INVALID_VOICE_ORDER_TRANSITION';
    end if;

    if p_state ->> 'restaurant_id' <> p_restaurant_id::text
       or p_state ->> 'conversation_id' <> p_conversation_id
       or (p_state ->> 'revision')::bigint <> next_revision then
        raise exception 'VOICE_ORDER_STATE_IDENTITY_MISMATCH';
    end if;

    if p_response ->> 'event_id' <> p_event_id
       or (p_response ->> 'state_revision')::bigint <> next_revision then
        raise exception 'VOICE_ORDER_RESPONSE_IDENTITY_MISMATCH';
    end if;

    -- Serializes first-write and retry races for one restaurant conversation.
    perform pg_advisory_xact_lock(
        hashtextextended(p_restaurant_id::text || ':' || p_conversation_id, 0)
    );

    select * into existing_event
      from public.voice_order_events
     where restaurant_id = p_restaurant_id
       and conversation_id = p_conversation_id
       and event_id = p_event_id;

    if found then
        if existing_event.request_hash <> p_request_hash then
            raise exception 'VOICE_ORDER_EVENT_PAYLOAD_MISMATCH';
        end if;

        select * into existing_session
          from public.voice_order_sessions
         where restaurant_id = p_restaurant_id
           and conversation_id = p_conversation_id;

        return query select true, existing_session.state, existing_event.response;
        return;
    end if;

    select * into existing_session
      from public.voice_order_sessions
     where restaurant_id = p_restaurant_id
       and conversation_id = p_conversation_id
     for update;

    if found then
        if existing_session.revision <> p_expected_revision then
            raise exception 'VOICE_ORDER_REVISION_CONFLICT';
        end if;

        update public.voice_order_sessions
           set revision = next_revision,
               state = p_state,
               expires_at = now() + interval '24 hours',
               updated_at = now()
         where restaurant_id = p_restaurant_id
           and conversation_id = p_conversation_id;
    else
        if p_expected_revision <> 0 then
            raise exception 'VOICE_ORDER_REVISION_CONFLICT';
        end if;

        insert into public.voice_order_sessions (
            restaurant_id,
            conversation_id,
            revision,
            state
        ) values (
            p_restaurant_id,
            p_conversation_id,
            next_revision,
            p_state
        );
    end if;

    insert into public.voice_order_events (
        restaurant_id,
        conversation_id,
        event_id,
        request_hash,
        state_revision,
        response
    ) values (
        p_restaurant_id,
        p_conversation_id,
        p_event_id,
        p_request_hash,
        next_revision,
        p_response
    );

    return query select false, p_state, p_response;
end;
$$;

revoke all on function public.save_voice_order_transition(
    uuid, text, bigint, jsonb, text, text, jsonb
) from public, anon, authenticated;

grant execute on function public.save_voice_order_transition(
    uuid, text, bigint, jsonb, text, text, jsonb
) to service_role;

create or replace function public.delete_expired_voice_order_sessions()
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
    deleted_count bigint;
begin
    delete from public.voice_order_sessions
     where expires_at < now();

    get diagnostics deleted_count = row_count;
    return deleted_count;
end;
$$;

revoke all on function public.delete_expired_voice_order_sessions()
    from public, anon, authenticated;

grant execute on function public.delete_expired_voice_order_sessions()
    to service_role;
