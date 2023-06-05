To get a quick feel for `Opsml`, run the following code in a new terminal. The following uses Mlflow as a ui interface

### Start Local Server

<div class="termy">

```console
$ export OPSML_TRACKING_URI="sqlite:///tmp.db"
$ export _MLFLOW_SERVER_FILE_STORE="sqlite:///tmp.db"
$ export OPSML_STORAGE_URI="./opsml"
$ export _MLFLOW_SERVER_ARTIFACT_DESTINATION="./opsml"
$ export _MLFLOW_SERVER_SERVE_ARTIFACTS="true"         # uses mlflow proxy

gunicorn \
      -k uvicorn.workers.UvicornWorker \
      --bind=0.0.0.0:8888 \
      "opsml.app.main:run_app(run_mlflow=True, login=False)"

<span style="color: green;">INFO</span>:     [INFO] Starting gunicorn 20.1.0
<span style="color: green;">INFO</span>:     [INFO] Listening at: http://0.0.0.0:8889
<span style="color: green;">INFO</span>:     [INFO] Using worker: uvicorn.workers.UvicornWorker
...
<span style="color: green;">INFO</span>:     [INFO] Application startup complete
```

</div>


Next, open a new terminal and run the following python script. Make sure to set the `OPSML_TRACKING_URI` as well


## Run Initial Python Script

```bash
export OPSML_TRACKING_URI=http://0.0.0.0:8888
```

```python

import pandas as pd
from sklearn.linear_model import LinearRegression
import numpy as np

from opsml.projects import ProjectInfo
from opsml.projects.mlflow import MlflowProject
from opsml.registry import DataCard, ModelCard


def fake_data():
    X_train = np.random.normal(-4, 2.0, size=(1000, 10))

    col_names = []
    for i in range(0, X_train.shape[1]):
        col_names.append(f"col_{i}")

    X = pd.DataFrame(X_train, columns=col_names)
    y = np.random.randint(1, 10, size=(1000, 1))
    return X, y


info = ProjectInfo(
    name="opsml",
    team="devops",
    user_email="test_email",
)

# start mlflow run
project = MlflowProject(info=info)
with project.run(run_name="test-run") as run:

    # create data and train model
    X, y = fake_data()
    reg = LinearRegression().fit(X.to_numpy(), y)

    # Create and registery DataCard with data profile
    data_card = DataCard(
        data=X,
        name="pipeline-data",
        team="mlops",
        user_email="mlops.com",
    )
    data_card.create_data_profile()
    run.register_card(card=data_card)

    # Create and register ModelCard with auto-converted onnx model
    model_card = ModelCard(
        trained_model=reg,
        sample_input_data=X[0:1],
        name="linear_reg",
        team="mlops",
        user_email="mlops.com",
        datacard_uid=data_card.uid,
        tags={"name": "model_tag"},
    )
    run.register_card(card=model_card)

    # log some metrics
    for i in range(0, 10):
        run.log_metric("mape", i, step=i)
```

## Server UI

Since we are using Mlflow as a ui, when you click the uri link, you should see something similar to the following.

### Project UI

Project UI lists all projects and recent runs

<p align="center">
  <img src="../images/mlflow_ui.png"  width="1512" height="402" alt="mlflow"/>
</p>

### Run UI

Within the run UI, you will see the various auto-recorded artifacts from your `Cards` and `Run`

<p align="center">
  <img src="../images/mlflow_run.png"  width="1841" height="792" alt="mlflow run"/>
</p>