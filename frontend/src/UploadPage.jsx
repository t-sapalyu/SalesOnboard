// Step 1 of onboarding: pick a CSV file, upload it, and check the first rows.
import { useState } from "react";

import { uploadFile } from "./api.js";

export default function UploadPage() {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null); // the backend's answer after upload

  async function handleUpload(event) {
    event.preventDefault();
    setError("");
    setUploading(true);
    try {
      setResult(await uploadFile(file));
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  if (result !== null) {
    return <Preview result={result} />;
  }

  return (
    <form onSubmit={handleUpload} className="card">
      <h2>Upload your sales file</h2>
      <input type="file" accept=".csv" onChange={(e) => setFile(e.target.files[0])} />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={!file || uploading}>
        {uploading ? "Uploading..." : "Upload"}
      </button>
    </form>
  );
}

function Preview({ result }) {
  return (
    <div className="card">
      <h2>{result.file_name}</h2>
      <p>
        {result.row_count} rows. The first {result.preview.length} rows, exactly as we read
        them:
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.preview.map((row, index) => (
              <tr key={index}>
                {result.columns.map((column) => (
                  <td key={column}>{row[column]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
