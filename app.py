import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from notion_client import Client

st.set_page_config(
    page_title="우리 결혼 준비",
    page_icon="💍",
    layout="wide"
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


@st.cache_data(ttl=300)
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


def make_event_text(row):
    return (
        f"<b>{row['이름']}</b><br>"
        f"예상: {won(row.get('예상금액'))}<br>"
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
    expected_events["이벤트"] = expected_events.apply(make_event_text, axis=1)

    actual_events = events.dropna(subset=["지출날짜"]).copy()
    actual_events["날짜"] = actual_events["지출날짜"]
    actual_events["이벤트"] = actual_events.apply(make_event_text, axis=1)

    event_by_date = pd.concat(
        [
            expected_events[["날짜", "이벤트"]],
            actual_events[["날짜", "이벤트"]],
        ],
        ignore_index=True,
    )

    if not event_by_date.empty:
        event_by_date = (
            event_by_date
            .groupby("날짜", as_index=False)["이벤트"]
            .agg(lambda s: "<br><br>".join(s))
        )

        daily = daily.merge(event_by_date, on="날짜", how="left")
    else:
        daily["이벤트"] = ""

    daily["이벤트"] = daily["이벤트"].fillna("")

    return daily


def draw_chart(df: pd.DataFrame, title: str):
    daily = make_cumulative_df(df)

    if daily.empty:
        st.warning(f"{title}: 그래프를 그릴 데이터가 없습니다.")
        return

    fig = go.Figure()

    line_styles = {
        "예상 지출": {
            "color": "rgba(220, 80, 80, 0.75)",
            "dash": "dash",
        },
        "실지출": {
            "color": "rgba(60, 120, 220, 0.9)",
            "dash": "solid",
        },
    }

    for name in ["예상 지출", "실지출"]:
        line_df = daily[daily["구분"] == name]

        fig.add_trace(
            go.Scatter(
                x=line_df["날짜"],
                y=line_df["누적지출금액"],
                mode="lines+markers",
                name=name,
                customdata=line_df[["이벤트"]],
                line=dict(
                    color=line_styles[name]["color"],
                    dash=line_styles[name]["dash"],
                    width=3,
                ),
                marker=dict(size=8),
                hovertemplate=(
                    "날짜: %{x|%Y-%m-%d}<br>"
                    f"{name} 누적: %{{y:,.0f}}원<br><br>"
                    "이벤트:<br>%{customdata[0]}"
                    "<extra></extra>"
                ),
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
        hovermode="x unified",
        font=dict(size=16),
        legend=dict(font=dict(size=15)),
    )

    fig.update_xaxes(title_font=dict(size=18), tickfont=dict(size=14))
    fig.update_yaxes(title_font=dict(size=18), tickfont=dict(size=14), tickformat=",")

    st.plotly_chart(fig, use_container_width=True)


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
        st.dataframe(df, use_container_width=True)