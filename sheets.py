"""Google Sheets access for the AJE files, as the recon-drive robot account."""
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from config import GOOGLE_KEY

API = "https://sheets.googleapis.com/v4/spreadsheets"


class Sheet:
    def __init__(self, spreadsheet_id):
        creds = service_account.Credentials.from_service_account_file(
            str(GOOGLE_KEY), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.http = AuthorizedSession(creds)
        self.id = spreadsheet_id

    def _check(self, r):
        if not r.ok:
            raise RuntimeError(f"Sheets API {r.status_code}: {r.text[:300]}")
        return r.json()

    def tabs(self):
        """{title: sheetId} in tab order."""
        data = self._check(self.http.get(f"{API}/{self.id}", params={"fields": "sheets.properties(sheetId,title,index)"}))
        return {s["properties"]["title"]: s["properties"]["sheetId"] for s in data["sheets"]}

    def grid(self, tab, rng="A1:H200"):
        """Rows of cells; each cell is {'v': entered value or formula, 'shown': displayed text}."""
        data = self._check(self.http.get(f"{API}/{self.id}", params={
            "ranges": f"'{tab}'!{rng}", "includeGridData": "true",
            "fields": "sheets.data.rowData.values(userEnteredValue,formattedValue)"}))
        rows = []
        for row in data["sheets"][0]["data"][0].get("rowData", []):
            cells = []
            for c in row.get("values", []):
                uev = c.get("userEnteredValue") or {}
                v = uev.get("formulaValue", uev.get("numberValue", uev.get("stringValue", uev.get("boolValue"))))
                cells.append({"v": v, "shown": c.get("formattedValue")})
            rows.append(cells)
        return rows

    def batch(self, requests):
        return self._check(self.http.post(f"{API}/{self.id}:batchUpdate", json={"requests": requests}))

    def values(self, tab, rng):
        data = self._check(self.http.get(f"{API}/{self.id}/values/'{tab}'!{rng}"))
        return data.get("values", [])
