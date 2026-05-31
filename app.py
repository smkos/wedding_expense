import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from notion_client import Client

st.set_page_config(
    page_title="우리 결혼 준비",
    page_icon="💍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

password = st.text_input(
    "우리만 아는 비밀번호를 입력해주세요 🔒",
    type="password"
)

if not password:
    st.stop()

if password != st.secrets["APP_PASSWORD"]:
    st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

if st.sidebar.button("🔄 데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()

notion = Client(auth=st.secrets["NOTION_TOKEN"])

DBS = {
    "결혼식 비용": st.secrets["WEDDING_COST_DB_ID"],
    "신혼집 비용": st.secrets["HOUSE_COST_DB_ID"],
}


def get_title(prop):
    if not prop or prop["type"] != "title":
        return ""
    return "".join(t["plain_text"] for t in prop["title"])


def get_number(prop):
    if not prop or prop["type"] != "number":
        return None
    return prop["number"]


def get_date(prop):
    if not prop or prop["type"] != "date":
        return None
    if prop["date"] is None:
        return None
    return prop["date"]["start"]


def get_rich_text(prop):
    if not prop or prop["type"] != "rich_text":
        return ""
    return "".join(t["plain_text"] for t in prop["rich_text"])


def won(v):
    if pd.isna(v):
        return "-"
    return f"{v:,.0f}원"


@st.cache_data(ttl=60)
def load_notion_db(db_id: str, db_name: str) -> pd.DataFrame:
    rows = []
    cursor = None

    while True:
        try:
            res = notion.data_sources.query(
                data_source_id=db_id,
                start_cursor=cursor,
            )
        except AttributeError:
            res = notion.databases.query(
                database_id=db_id,
                start_cursor=cursor,
            )

        for page in res["results"]:
            props = page["properties"]

            rows.append({
                "DB": db_name,
                "이름": get_title(props.get("이름")),
                "예상금액": get_number(props.get("예상금액")),
                "예상지출날짜": get_date(props.get("예상지출날짜")),
                "실지출금액": get_number(props.get("실지출금액")),
                "지출날짜": get_date(props.get("지출날짜")),
                "비고": get_rich_text(props.get("비고")),
            })

        if not res.get("has_more"):
            break

        cursor = res["next_cursor"]

    return pd.DataFrame(rows)


def make_expected_event_text(row):
    return (
        f"<b>{row['이름']}</b><br>"
        f"예상: {won(row.get('예상금액'))}"
    )

def make_actual_event_text(row):
    return (
        f"<b>{row['이름']}</b><br>"
        f"실지출: {won(row.get('실지출금액'))}"
    )

def make_cumulative_df(df: pd.DataFrame) -> pd.DataFrame:
    expected = df[["예상지출날짜", "예상금액"]].copy()
    expected.columns = ["날짜", "금액"]
    expected["구분"] = "예상 지출"

    actual = df[["지출날짜", "실지출금액"]].copy()
    actual.columns = ["날짜", "금액"]
    actual["구분"] = "실지출"

    long_df = pd.concat([expected, actual], ignore_index=True)
    long_df["날짜"] = pd.to_datetime(long_df["날짜"], errors="coerce")
    long_df["금액"] = pd.to_numeric(long_df["금액"], errors="coerce")
    long_df = long_df.dropna(subset=["날짜", "금액"])

    daily = (
        long_df
        .groupby(["날짜", "구분"], as_index=False)["금액"]
        .sum()
        .sort_values("날짜")
    )

    daily["누적지출금액"] = daily.groupby("구분")["금액"].cumsum()

    events = df.copy()
    events["예상지출날짜"] = pd.to_datetime(events["예상지출날짜"], errors="coerce")
    events["지출날짜"] = pd.to_datetime(events["지출날짜"], errors="coerce")

    expected_events = events.dropna(subset=["예상지출날짜"]).copy()
    expected_events["날짜"] = expected_events["예상지출날짜"]
    expected_events["구분"] = "예상 지출"
    expected_events["이벤트"] = expected_events.apply(
        make_expected_event_text,
        axis=1,
    )

    actual_events = events.dropna(subset=["지출날짜"]).copy()
    actual_events["날짜"] = actual_events["지출날짜"]
    actual_events["구분"] = "실지출"
    actual_events["이벤트"] = actual_events.apply(
        make_actual_event_text,
        axis=1,
    )

    event_by_date = pd.concat(
        [
            expected_events[["날짜", "구분", "이벤트"]],
            actual_events[["날짜", "구분", "이벤트"]],
        ],
        ignore_index=True,
    )

    event_by_date = event_by_date.drop_duplicates(
        subset=["날짜", "구분", "이벤트"]
    )

    if not event_by_date.empty:
        event_by_date = (
            event_by_date
            .groupby(["날짜", "구분"], as_index=False)["이벤트"]
            .agg(lambda s: "<br><br>".join(s))
        )

        daily = daily.merge(
            event_by_date,
            on=["날짜", "구분"],
            how="left",
        )
    else:
        daily["이벤트"] = ""

    daily["이벤트"] = daily["이벤트"].fillna("")

    return daily


def draw_chart(df: pd.DataFrame, title: str):
    daily = make_cumulative_df(df)

    if daily.empty:
        st.warning(f"{title}: 그래프를 그릴 데이터가 없습니다.")
        return

    pivot = daily.pivot_table(
        index="날짜",
        columns="구분",
        values="누적지출금액",
        aggfunc="last",
    ).sort_index()

    pivot = pivot.ffill().fillna(0).reset_index()

    event_pivot = daily.pivot_table(
        index="날짜",
        columns="구분",
        values="이벤트",
        aggfunc="first",
    ).reset_index()

    hover_df = pivot.merge(event_pivot, on="날짜", suffixes=("", "_이벤트"))
    hover_df = hover_df.sort_values("날짜")

    fig = go.Figure()

    expected_df = daily[daily["구분"] == "예상 지출"]
    actual_df = daily[daily["구분"] == "실지출"]

    fig.add_trace(
        go.Scatter(
            x=expected_df["날짜"],
            y=expected_df["누적지출금액"],
            mode="lines+markers",
            name="예상 지출",
            line=dict(
                color="rgba(220, 80, 80, 0.75)",
                dash="dash",
                width=3,
            ),
            marker=dict(size=8),
            hoverinfo="skip",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=actual_df["날짜"],
            y=actual_df["누적지출금액"],
            mode="lines+markers",
            name="실지출",
            line=dict(
                color="rgba(60, 120, 220, 0.9)",
                dash="solid",
                width=3,
            ),
            marker=dict(size=8),
            hoverinfo="skip",
        )
    )

    for col in ["예상 지출", "실지출"]:
        if col not in hover_df.columns:
            hover_df[col] = pd.NA

    for col in ["예상 지출_이벤트", "실지출_이벤트"]:
        if col not in hover_df.columns:
            hover_df[col] = ""

    hover_df["hover_text"] = (
        "<span style='color:#dc5050'>●</span> "
        "예상 지출 누적: "
        + hover_df["예상 지출"].apply(won)
        + "<br>"
        + "<span style='color:#3c78dc'>●</span> "
        "실지출 누적: "
        + hover_df["실지출"].apply(won)
        + "<br><br>"
        + "<b>예상 지출</b><br>"
        + hover_df["예상 지출_이벤트"].fillna("")
        + "<br><br>"
        + "<b>실지출</b><br>"
        + hover_df["실지출_이벤트"].fillna("")
    )

    fig.add_trace(
        go.Scatter(
            x=hover_df["날짜"],
            y=hover_df[["예상 지출", "실지출"]].fillna(0).max(axis=1),
            mode="markers",
            marker=dict(size=20, opacity=0),
            showlegend=False,
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_df["hover_text"],
        )
    )

    today = pd.Timestamp.today().normalize()
    x_max = daily["날짜"].max()
    today_line_x = min(today, x_max)

    fig.add_shape(
        type="line",
        x0=today_line_x.to_pydatetime(),
        x1=today_line_x.to_pydatetime(),
        y0=0,
        y1=1,
        yref="paper",
        line=dict(dash="dash", width=2),
    )

    fig.add_annotation(
        x=today_line_x.to_pydatetime(),
        y=1,
        yref="paper",
        text="Today",
        showarrow=False,
        yanchor="bottom",
        font=dict(size=15),
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=24)),
        xaxis_title="날짜",
        yaxis_title="누적 지출 금액",
        hovermode="x",
        font=dict(size=16),
        legend=dict(font=dict(size=15)),
    )

    fig.update_xaxes(title_font=dict(size=18), tickfont=dict(size=14))
    fig.update_yaxes(title_font=dict(size=18), tickfont=dict(size=14), tickformat=",")

    st.plotly_chart(fig, width="stretch")


st.title("결혼 준비 비용 대시보드")

all_dfs = {}

for db_name, db_id in DBS.items():
    all_dfs[db_name] = load_notion_db(db_id, db_name)

for db_name, df in all_dfs.items():
    st.subheader(db_name)

    expected_total = df["예상금액"].fillna(0).sum()
    actual_total = df["실지출금액"].fillna(0).sum()
    remaining_total = expected_total - actual_total

    col1, col2, col3 = st.columns(3)
    col1.metric("예상 총액", won(expected_total))
    col2.metric("실지출 총액", won(actual_total))
    col3.metric("남은 비용", won(remaining_total))

    draw_chart(df, db_name)

    with st.expander(f"{db_name} 원본 데이터 보기"):
        st.dataframe(df, width="stretch")