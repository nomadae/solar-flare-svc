"""
Script to get the data from noa web site and adding some new fields
to struct the data to make available to the community.
This software is available under GPL License. 
author@: José Vidal Cardona Rosas - ladivcr@comunidad.unam.mx
Year: 2021
License: GNU GENERAL PUBLIC LICENSE (GPL)
"""

from bs4 import BeautifulSoup
from datetime import tzinfo
from datetime import timedelta
from datetime import datetime
import json
import requests
import mysql.connector
from pysolar import solar
from dateutil import parser
import os
from dotenv import load_dotenv
import logging 


logging.basicConfig(level=logging.DEBUG, filename="logs.log")
load_dotenv()

class NoaData:
    def __init__(self, url):
        self.url = url
        self.db_host = os.getenv('HOST')
        self.db_port = os.getenv('PORT')
        self.db_pwd = os.getenv('PASSWORD')
        self.db_name = os.getenv('DATABASE')
        self.db_user = os.getenv('USERDB')
        self.today = datetime.now()
    def get_xray_flares_latest(self):
        """function to get the data from
        noa web page"""

        noa = requests.get(self.url)
        labels = BeautifulSoup(noa.text, 'html.parser')
        a_labels_href = labels.find_all("a", href=True)
        for item in a_labels_href:

            if item["href"] == 'xray-flares-latest.json':
                focus_link = item["href"]
                # construir url de importancia
                focus_page = self.url+focus_link
                break
        else:
            logging.error(f"{self.today} - error: El enlace para xray-flares-latest cambio")

        try:
            flares_data = requests.get(focus_page) # obtener elementos
            # parsear elementos
            flares_data = flares_data.text
            data = json.loads(flares_data[1:-1]) # obtener elementos
        except Exception as e:
            logging.error(f"{self.today} - error: Durante la obtención de datos - {e}")
            return {}

        return data


    def struct_data(self, data):
        """function to parsing and struct the data
        to upload in db"""

        instrument = data.get("satellite", None)
        classification = data.get("max_class", None)

        # coordenadas de la aproximación al centro de México
        LAT = 24.05754867
        LOW = -104.0226393
        t_max = parser.parse(data.get("max_time", None))
        altitude = solar.get_altitude(LAT, LOW, t_max)
        azimuth = solar.get_azimuth(LAT, LOW, t_max)

        # struct the data in a dictionary
        data_to_upload_db = {
                "event_start" : data.get("begin_time", None),
                "max_peak_event" : data.get("max_time", None),
                "event_finish" : data.get("end_time", None),
                "classification": classification[0],
                "sub_classification": classification[1:],
                "observation_instrument": f"G{instrument}",
                "max_energy": data.get("max_xrlong", None),
                "total_energy": "0",
                "active_region": "0",
                "latitude": 0.0,
                "longitude": 0.0,
                "altitude": altitude,
                "azimuth": azimuth
                }
        logging.info(f"{self.today} - Datos estructurados con éxito")
        return data_to_upload_db

    def _check_duplicate_data(self, eventStart):
        cnx = mysql.connector.connect(user=self.db_user, password=self.db_pwd, host=self.db_host, database=self.db_name)
        cursor = cnx.cursor()
        query = ("SELECT event_start FROM observations WHERE event_start = %s;")
        try:
            data_query = (eventStart,)
            cursor.execute(query, data_query)
            response = cursor.fetchall()
            cnx.commit()
            if len(response) >= 1:
                return True
            
        except Exception as e:
            logging.error(f"{self.today} - Ha ocurrido un error al revisar duplicados: {e}")
            return True

        return False

    def upload_data(self, data):
        """function to make a connection with db and upload
        the data"""
        
                
        cnx = mysql.connector.connect(user=self.db_user, password=self.db_pwd, host=self.db_host, database=self.db_name)
        cursor = cnx.cursor()

        query = ("INSERT INTO observations(\
        event_start,\
        max_peak_event,\
        event_finish,\
        classification,\
        sub_classification,\
        observation_instrument,\
        max_energy,\
        total_energy,\
        active_region,\
        altitude,\
        azimuth,\
        latitude,\
        longitude,\
        created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);");
        
        eventStart = parser.parse(data.get("event_start"))
        maxPeakEvent = parser.parse(data.get("max_peak_event"))
        eventFinish = parser.parse(data.get("event_finish"))
        classification = data.get("classification")
        subClassification = data.get("sub_classification")
        observationInstrument = data.get("observation_instrument")
        maxEnergy = data.get("max_energy")
        totalEnergy = data.get("total_energy")
        activeRegion = data.get("active_region")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        altitude = data.get("altitude")
        azimuth = data.get("azimuth")
        created_at = datetime.now()
        
        data_query = (eventStart, maxPeakEvent, eventFinish,
                classification, subClassification, observationInstrument,
                maxEnergy, totalEnergy, activeRegion, altitude, azimuth, latitude, longitude, created_at)
        try:
            duplication = self._check_duplicate_data(eventStart)
            if duplication is True:
                logging.info(f"{self.today} - Los datos obtenidos ya estan registrados en la base de datos")
                return 
            cursor.execute(query, data_query)
            cnx.commit()
            logging.info(f"{self.today} - Datos cargados con éxito - 201")
        except Exception as e: 
            logging.error(f"{self.today} - Error al ejecutar la query: {e}")
            return False

        return 

if __name__=="__main__":
    Noa = NoaData(url = "https://services.swpc.noaa.gov/json/goes/primary/")
    data = Noa.get_xray_flares_latest()
    data_db = Noa.struct_data(data)
    Noa.upload_data(data_db)
