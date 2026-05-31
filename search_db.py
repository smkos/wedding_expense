import streamlit as st
from notion_client import Client

notion = Client(auth=st.secrets["NOTION_TOKEN"])

result = notion.search(
    filter={
        "property": "object",
        "value": "data_source"
    }
)

st.write("접근 가능한 데이터 소스 목록")

for ds in result["results"]:
    title = ""

    if ds.get("title"):
        title = ds["title"][0]["plain_text"]

    st.write("TITLE:", title)
    st.write("ID:", ds["id"])
    st.write("---")