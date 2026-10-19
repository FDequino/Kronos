"""Meta-modelo de ensemble (stacking, segunda capa) sobre las features
tabulares de portfolio/features.py + la señal de Chronos-Bolt -- aprende en
que condiciones conviene confiar en el pronostico de Chronos y en cuales no.

Arquitectura de Meta-Labeling (Lopez de Prado, Fase 5, ver conversacion
"evolucion integral" y portfolio/backtest.py::build_anchor_dataset/
_triple_barrier_label): el label y* que entrena este modelo YA NO es "el
precio subio a horizonte fijo" -- es "la direccion que Chronos (el modelo
PRIMARIO) propuso llego a tocar su Barrera Superior (Triple Barrier) antes
que la Inferior o la Vertical". Este modelo (el SECUNDARIO) no decide una
direccion propia: solo aprende P(y*=1 | features) = que tan confiable es
CONFIRMAR la propuesta de Chronos en esas condiciones -- degradar a
'mantener' cuando esa probabilidad es baja (ver backtest.py::summarize,
META_LABEL_CONFIDENCE_THRESHOLD) es la pieza de "filtro de confianza" pedida
explicitamente.

Fase 2 (regularizacion, ver conversacion): la ronda anterior entrenaba UN
modelo por ticker con solo cientos de filas cada uno -- overfitting real,
confirmado (el ensemble empeoraba la precision promedio 53.2% -> 47.5%).
Ahora es un UNICO modelo global (Cross-Sectional Pooling, ver
pool_anchor_frames) entrenado con los anchors de los 12 activos juntos --
miles de filas en vez de cientos, misma logica de que es "una condicion
tecnica/de volatilidad confiable" aprendida sobre mucha mas evidencia en vez
de memorizar el ruido particular de un solo activo.

Vive en services/predictor/ (junto al motor base) pero NO depende de
fastapi/torch ni de nada del resto de ese paquete -- es un modulo standalone
de scikit-learn, pensado para importarse directo desde portfolio/backtest.py
(que corre en el venv principal, no en el del microservicio). Separar los
imports importa: main.py (fastapi/torch) solo carga en el venv del
microservicio; este archivo solo necesita numpy/pandas/scikit-learn, que ya
estan en el venv principal.

Nota de hardware, declarada en vez de simulada: no hay aceleracion MPS/Metal
real para un ensemble de arboles -- esa aceleracion es de redes neuronales
(la usa Chronos-Bolt, ver main.py), no de scikit-learn ni de XGBoost (que
tampoco tiene backend de GPU para Apple Silicon, solo CUDA). Lo que SI es
real y esta implementado aca es paralelismo genuino de CPU (n_jobs=-1),
que en un M4 reparte el entrenamiento entre todos los nucleos disponibles.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from portfolio.features import FEATURE_COLUMNS

# min_samples_leaf subido de 10 a 50 y max_depth bajado de 5 a 4: con el
# dataset pooled (miles de filas en vez de cientos) hay margen para exigir
# hojas mas grandes sin quedarse sin splits -- es la palanca mas directa
# contra overfitting en un arbol, y ahora el dataset la banca. Con solo 4
# features (antes 16) el riesgo de sobreajuste ya baja de entrada, esto es
# una vuelta de tuerca extra, no la correccion principal.
DEFAULT_PARAMS = dict(
    n_estimators=500,
    max_depth=4,
    min_samples_leaf=50,
    class_weight="balanced",
    n_jobs=-1,
    random_state=42,
)


def pool_anchor_frames(frames):
    """Cross-Sectional Pooling: concatena los anchors de varios tickers en un
    unico dataset, ordenado por fecha (NUNCA por ticker) -- ese orden
    temporal global es lo que garantiza, aguas abajo en el walk-forward
    pooled (ver backtest.py::walk_forward_evaluate_pooled), que un fold de
    entrenamiento jamas incluya un anchor de NINGUN ticker cuya fecha sea
    posterior a la del bloque de test de ese fold. Cada frame debe traer ya
    una columna 'symbol' y una columna 'date' (ver backtest.py::
    build_anchor_dataset)."""
    pooled = pd.concat(frames, ignore_index=True)
    return pooled.sort_values("date").reset_index(drop=True)


class MetaLearner:
    """Envoltorio fino sobre RandomForestClassifier -- fit/predict_proba_up
    siempre sobre un DataFrame con columnas feature_columns (nunca arrays
    sueltos, para no perder de vista que columna es cual).

    feature_columns configurable (Fase 3, ver conversacion): antes estaba
    fijo al modulo entero (FEATURE_COLUMNS importado a nivel de archivo) --
    ahora es un parametro de instancia para poder entrenar, en el MISMO
    fold del walk-forward, un modelo V2 (sin macro), uno V3 (con macro) y
    uno V4 (+RSI/MACD/ATR%%/FFD, Fase 4, ver portfolio/features.py) y
    compararlos de verdad en igualdad de condiciones (mismo split de datos,
    mismos hiperparametros, solo cambian las columnas). Default:
    FEATURE_COLUMNS (V4, el set mas completo).

    RandomForest en vez de XGBoost a proposito: cero dependencia nueva
    pesada (scikit-learn ya es parte del proyecto), y con las ventanas de
    entrenamiento chicas que da un walk-forward (ver backtest.py) un
    RandomForest con max_depth/min_samples_leaf acotados sobreajusta menos
    por default que un gradient boosting sin tunear."""

    def __init__(self, feature_columns=None, **params):
        self.feature_columns = list(feature_columns) if feature_columns is not None else list(FEATURE_COLUMNS)
        self.params = {**DEFAULT_PARAMS, **params}
        self.model = RandomForestClassifier(**self.params)
        self.fitted = False
        self.majority_class = None

    def fit(self, X, y):
        X = self._prepare(X)
        y = np.asarray(y)
        if len(np.unique(y)) < 2:
            # Una sola clase en la ventana de entrenamiento -- pasa en los
            # primeros folds del walk-forward, cuando todavia hay pocos
            # anchors resueltos. Se declara explicito (predict_proba_up cae a
            # la clase mayoritaria) en vez de dejar que sklearn tire un error
            # poco claro o, peor, entrenar igual sobre una sola clase.
            self.fitted = False
            self.majority_class = int(y[0]) if len(y) else 1
            return self
        self.model.fit(X, y)
        self.fitted = True
        return self

    def predict_proba_up(self, X):
        """P(clase=1) por fila -- 1 = el precio real a horizon_days superó el
        hurdle (ver backtest.py::compute_hurdle_pct). 0.5 fijo si nunca se
        pudo entrenar (sin evidencia ni para una clase mayoritaria)."""
        X = self._prepare(X)
        if not self.fitted:
            if self.majority_class is None:
                return np.full(len(X), 0.5)
            return np.full(len(X), float(self.majority_class))
        proba = self.model.predict_proba(X)
        classes = list(self.model.classes_)
        if 1 not in classes:
            return np.zeros(len(X))
        return proba[:, classes.index(1)]

    def feature_importances(self):
        if not self.fitted:
            return {}
        return dict(
            sorted(zip(self.feature_columns, self.model.feature_importances_), key=lambda kv: -kv[1])
        )

    def _prepare(self, X):
        return X[self.feature_columns].fillna(0.0)
