-- Запустити в Supabase → SQL Editor

create table if not exists containers (
  id               uuid        default gen_random_uuid() primary key,
  number           text        unique not null,
  cargo_name       text        default 'Вантаж',
  batch            text        default '',
  weight           text        default '',
  status           text        default 'UNKNOWN',
  vessel_name      text,
  current_location text,
  destination      text,
  eta              text,
  etd              text,
  last_event       text,
  last_updated     timestamptz default now(),
  created_at       timestamptz default now()
);

alter table containers enable row level security;

create policy "Public select"  on containers for select  using (true);
create policy "Service insert" on containers for insert  with check (true);
create policy "Service update" on containers for update  using (true);
create policy "Service delete" on containers for delete  using (true);
