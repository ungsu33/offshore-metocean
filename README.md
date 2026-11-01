# Offshore Metocean

Python 데스크톱 GUI로 해상풍력의 **바람·파랑 환경조건 후보**와 산정근거를 검토하는 도구입니다. 한국 해역을 우선 대상으로, 좌표·자료·설계 정의를 입력하고 자료 비교, 조건부 통계, 개별 극치 분석 및 DLC 매핑 후보를 표·보고서로 출력합니다.

## 실행

Python 3.11 이상을 사용합니다. Windows에서는 저장소를 받은 뒤 다음을 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m offshore_metocean gui
```

설치 후에는 **`launch_gui.bat`를 더블클릭**해 실행할 수 있습니다. `setup.ps1 -PythonExe 'C:\path\to\python.exe'`로 Python 경로를 지정해 설치해도 됩니다.

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/offshore-metocean-gui
```

## GUI 사용 순서

1. **프로젝트**: 위도·경도, 목표 풍속 높이·평균시간, 목표 파주기와 기준 검토 기록 입력.
2. **자료 & 메타데이터**: 주자료·비교 hindcast·관측 파일 선택. 원래 대기/파랑/강제력 격자, 제공 격자, 시간 정의, 모델 버전·동질성·지점 대표성의 근거 입력. CSV 열 이름은 GUI에서 매핑 가능.
3. **분석 옵션**: Gumbel/GEV, 재현기간, 유효 연수, 결측 기준, bootstrap·신뢰수준, 비교 오차·공간/시간 상한 설정. 초기 수치는 설계기준의 확정 요구값이 아닙니다.
4. **DLC 규칙**: 운영상황 템플릿을 프로젝트의 DLC와 연결. 기준명·판본·원문 조항을 확인한 경우만 체크.
5. **분석 실행**: 차트, 변수별 적합성·사유, 검증 지표, 풍속별 파고·주기 분포, 결합빈도, 극치 후보와 연최대 자료 충족률 확인.
6. **검토·출력**: 선택 후보의 예비 채택/반려 기록 후 Excel·CSV·HTML 보고서·설정·체크섬 manifest 내보내기. 합성 예제·보류 자료·원문 매핑 미확인 행은 설계값 채택이 차단됩니다.

화면 위 **합성 예제** → **분석 실행**으로 실제 자료 없이 전체 흐름을 확인할 수 있습니다. 합성값은 어떤 실제 좌표의 관측·hindcast도 아니며 설계용으로 채택할 수 없습니다. 입력 변경 시 이전 결과의 검토·내보내기는 무효화됩니다.

## 입력 자료

표준 CSV 열:

```csv
timestamp,wind_speed,hs,tp,wind_dir,wave_dir
```

- `timestamp`: 원래 시간대를 메타데이터에 명시. UTC로 정합하며 중복 시각은 오류 처리합니다.
- `wind_speed`: m/s, `hs`: m, `tp`: s, 방향: 진북 기준 degrees. 다른 단위는 외부에서 근거를 남겨 변환하거나 입력 단위를 명시해 보류 처리하세요.
- `tp`는 내부 열 이름이며 물리적 주기의 정의는 별도입니다. `Tp/Tm01/Tm02/Tz`를 혼용하지 않습니다.
- CSV는 UTF-8로 저장합니다. `u10/v10`가 있으면 풍속 크기와 기상학적 from 방향을 계산합니다.
- NetCDF는 1차원 latitude/longitude 좌표의 최근접 점을 선택하고 점·거리·파일 해시를 기록합니다. 해상점을 임의로 찾아 바꾸거나 연안 다운스케일링하지 않습니다. 선택점의 지형·수심·차폐 대표성을 검토해야 합니다.
- 여러 월별 또는 대기/파랑 분리 파일은 시간·변수별로 결합합니다. 충돌 값, ensemble/level/expver 혼합, 선언과 다른 NetCDF 단위는 자동으로 넘기지 않습니다.
- 샘플 설정은 `examples/demo_project.json`, 자료 메타데이터 양식은 `examples/source_metadata.json`을 참고하세요. 후자는 실제 자료의 사양으로 채워야 합니다.

## 격자·평균시간·검증 원칙

**모델 적분 간격 / 자료 제공 간격 / 변수 평균시간**과 **native 대기·파랑·강제력 격자 / 배포 격자**를 각각 저장·평가합니다. 미세한 배포 격자가 거친 원래 모델의 물리 해상도를 개선한 것으로 표시하지 않습니다.

평균시간 변환은 수행하지 않습니다. 특히 시간별 순간 풍속을 1시간 평균이나 10분 평균으로 표시하지 않습니다. 높이 보정은 선택한 power law와 전단 지수·근거가 모두 있는 경우만 적용합니다.

검증은 동일 시각의 비교 가능한 정의로 수행합니다. 풍속 높이·평균시간·파주기가 다르면 해당 변수는 비교 검증 근거로 사용하지 않습니다. 방향 오차는 원형 차이로 평가합니다. NRMSE는 선형 변수의 경우 비교자료 절대평균으로, 방향은 180°로 정규화하며 임계값은 사용자 설정입니다.

| 상태 | 의미 |
|---|---|
| 채택 후보 | 관측 비교·검토된 수치 기준 등 현재 screening 요건 충족. 최종 설계 승인·인증 합격을 뜻하지 않음 |
| 잠정 | 복수 자료 비교 등의 근거로 개념·기본설계용 예비 검토만 허용. 상세설계·인증용 채택 보류 |
| 산정 보류 | 정의·자료·대표성·검증·표본 등의 근거 부족. 이유와 종속 출력의 부족을 표시 |
| 합성 예제 | 기능 확인용 생성 자료. 설계 채택 불가 |

관측과 hindcast 또는 두 hindcast가 같은 동화·바람 강제력·모델 계보를 공유하는지는 별도 근거로 확인해야 합니다. 교차모델 일치를 독립 관측 검증으로 간주하지 않습니다.

## 분석 범위와 한계

- 운영단계의 정상운전·시동정지·고장/비상정지·정지대기 상황을 다루는 **환경입력 템플릿**입니다. `OP-*`는 IEC DLC 번호가 아닙니다.
- 풍속별 `Hs–T` 요약과 3변수 결합빈도, 풍향·파향 교차빈도, 연간 자료 충족률을 통과한 연최대 Gumbel/GEV 적합과 bootstrap 신뢰구간을 제공합니다.
- 연최대 분포의 재현기간은 `P(annual maximum > level)=1/T`, `T>1`로 정의합니다. 1년 조건을 이 식에 대입하지 않습니다. GEV shape는 SciPy의 부호 관례를 사용합니다.
- 극한 조합은 개별 극값과 관측기간 상위 5% 경험표본의 동반 주기·방향을 연결하는 **예비 시나리오**입니다. 공동 T년 사건, 환경등고선, 극한영역 조건부 외삽, 응답 지배조건을 계산했다고 주장하지 않습니다.
- 태풍·비태풍 분리, 비정상 극치·기후변화, 편향 보정, 고해상도 모델 실행, 난류/돌풍 시계열, OEM 제어·고장 모델은 자동 처리하지 않습니다.
- 전체 Design Basis에는 조류·수위·지반·구조응답 등 별도 입력과 검토가 필요합니다. 기준의 상세 조항을 확보·검토하지 않은 행은 원문 매핑 미확인으로 유지합니다.

## ERA5와 국내외 자료

**데이터 소스** 화면에서 공식 문서를 열거나 ERA5의 월별 API 요청 계획을 저장·실행할 수 있습니다. CDS 계정의 개인 토큰과 데이터셋 이용약관 동의는 사용자가 준비해야 합니다. 인증정보는 사용자 `.cdsapirc` 또는 `CDSAPI_URL/CDSAPI_KEY`에서 읽으며 프로젝트에 저장하지 않습니다. 대기·파랑은 분리 요청하고 다운로드 캐시의 NetCDF 구조를 확인합니다.

ERA5 풍속은 순간값이므로 기본 목표인 10분 평균 허브풍속에 자동 채택되지 않습니다. API 다운로드가 됐다는 사실이 자료의 설계 적합성 통과를 의미하지 않습니다. 기상청·NOAA의 확보 파일은 CSV/NetCDF로 불러올 수 있으며, 이 버전에는 기상청·NOAA 계정 API 자동 다운로드 기능이 포함되지 않습니다.

## CLI / 자동 검사

```powershell
.\.venv\Scripts\python.exe -m offshore_metocean demo --output results
.\.venv\Scripts\python.exe -m offshore_metocean run projects\site.local.json --output results
.\.venv\Scripts\python.exe -m offshore_metocean template
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

설정 파일의 상대 입력 경로는 해당 설정 파일이 있는 폴더를 기준으로 해석합니다. `demo_mode=true`인 설정은 고정 seed의 합성 자료를 재생성합니다. 같은 입력·설정·버전·seed의 분석 수치는 재현되며 출력 파일의 생성 시각은 달라집니다.

GitHub Actions는 Windows/Linux에서 정적 검사와 분석·데이터 입출력·GUI 테스트를 실행합니다. `data/`, `projects/`, `results/`, `.venv/`, API 키·토큰·NetCDF·Excel 파일은 Git 추적에서 제외됩니다. 실제 프로젝트 파일의 버전 관리는 해당 자료의 공개 가능 여부를 확인해 별도로 구성하세요.

## 기술 근거

- [ERA5 documentation](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5%2Bdata%2Bdocumentation)
- [CDS API setup](https://cds.climate.copernicus.eu/how-to-api)
- [기상청 API허브](https://apihub.kma.go.kr/apiInfo.do)
- [NOAA wave model / hindcast archives](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/wavemodels.php)
- [IEC 61400-3-1](https://webstore.iec.ch/en/publication/29360), [DNV-ST-0437](https://www.dnv.com/energy/standards-guidelines/dnv-st-0437-loads-and-site-conditions-for-wind-turbines/), [DNV-RP-C205](https://www.dnv.com/energy/standards-guidelines/dnv-rp-c205-environmental-conditions-and-environmental-loads/)
- [SciPy GEV](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.genextreme.html), [Qt threads](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html)

공식 개요와 API/라이브러리 문서를 참고한 구현입니다. 기준 원문의 저작권 자료를 이 저장소에 복제하지 않습니다.
